"""음성 출력: G1 스피커(AudioClient.PlayStream) 재생, 또는 WAV 파일 저장(로봇 없이 시험할 때).

PlayStream 사용 조건(G1 음성 서비스 문서)
- 입력은 16 kHz, 모노, 16비트 PCM이다.
- 같은 stream_id로 보낸 청크는 이어서 재생되고, 다른 stream_id는 현재 재생을 끊고 시작한다.
  그래서 응답 하나마다 stream_id 하나를 쓴다.

재생 중 입력 차단(반이중 동작)
- 로봇 스피커 소리가 로봇 마이크로 다시 들어가면 음성 모델이 자기 목소리에 대답한다.
- busy()가 참인 동안(재생 대기 중이거나 재생이 끝난 뒤 tail_s 이내) 마이크 입력을 보내지 않는다.
"""
from __future__ import annotations

import asyncio
import time
from typing import Callable, Optional

from .audio_utils import ROBOT_RATE, BYTES_PER_SAMPLE, pcm_duration_s, write_wav


class BaseSpeaker:
    def __init__(self, min_chunk_s: float = 0.3, max_ahead_s: float = 3.0, tail_s: float = 0.5):
        self.min_chunk_bytes = int(min_chunk_s * ROBOT_RATE) * BYTES_PER_SAMPLE
        self.max_ahead_s = max_ahead_s
        self.tail_s = tail_s
        self.play_end = 0.0                     # 추정 재생 종료 시각(time.monotonic 기준)
        self.on_first_play: Optional[Callable[[float], None]] = None
        self._queue: asyncio.Queue = asyncio.Queue()
        self._buf = bytearray()
        self._stream_id: Optional[str] = None
        self._first_sent = False
        self._worker: Optional[asyncio.Task] = None

    # ---- 외부 호출용 -------------------------------------------------
    def start(self) -> None:
        self._worker = asyncio.create_task(self._run(), name="speaker-worker")

    def begin_response(self) -> None:
        self._queue.put_nowait(("begin", None))

    def write(self, pcm16k: bytes) -> None:
        if pcm16k:
            self._queue.put_nowait(("audio", pcm16k))

    def end_response(self) -> None:
        self._queue.put_nowait(("end", None))

    async def stop(self) -> None:
        """재생을 즉시 멈추고 대기 중인 음성을 버린다(끼어들기 처리용)."""
        while not self._queue.empty():
            self._queue.get_nowait()
        self._buf.clear()
        self._stream_id = None
        await self._stop_impl()
        self.play_end = time.monotonic()

    def busy(self, now: Optional[float] = None) -> bool:
        now = time.monotonic() if now is None else now
        return (not self._queue.empty()) or len(self._buf) > 0 or now < self.play_end + self.tail_s

    async def close(self) -> None:
        if self._worker:
            self._worker.cancel()

    # ---- 내부 동작 ---------------------------------------------------
    async def _run(self) -> None:
        while True:
            kind, data = await self._queue.get()
            if kind == "begin":
                self._buf.clear()
                self._stream_id = str(int(time.time() * 1000))
                self._first_sent = False
            elif kind == "audio":
                if self._stream_id is None:            # begin 없이 음성이 오면 새 응답으로 취급
                    self._stream_id = str(int(time.time() * 1000))
                    self._first_sent = False
                self._buf.extend(data)
                if len(self._buf) >= self.min_chunk_bytes:
                    await self._send(bytes(self._buf))
                    self._buf.clear()
            elif kind == "end":
                if self._buf and self._stream_id is not None:
                    await self._send(bytes(self._buf))
                self._buf.clear()
                self._stream_id = None

    async def _send(self, chunk: bytes) -> None:
        # 로봇 쪽에 너무 많이 쌓이지 않도록 재생 위치보다 max_ahead_s 이상 앞서지 않게 기다린다.
        while self.play_end - time.monotonic() > self.max_ahead_s:
            await asyncio.sleep(0.05)
        t_call = time.monotonic()
        await self._play_impl(self._stream_id, chunk)
        start = max(self.play_end, t_call)
        self.play_end = start + pcm_duration_s(chunk)
        if not self._first_sent:
            self._first_sent = True
            if self.on_first_play:
                self.on_first_play(t_call)

    async def _play_impl(self, stream_id: str, chunk: bytes) -> None:
        raise NotImplementedError

    async def _stop_impl(self) -> None:
        raise NotImplementedError


class RobotSpeaker(BaseSpeaker):
    """G1 스피커. ChannelFactoryInitialize()가 먼저 호출되어 있어야 한다."""

    def __init__(self, app_name: str = "llm_chat", volume: Optional[int] = None, **kw):
        super().__init__(**kw)
        from unitree_sdk2py.g1.audio.g1_audio_client import AudioClient

        self.app_name = app_name
        self.client = AudioClient()
        self.client.SetTimeout(10.0)
        self.client.Init()
        if volume is not None:
            self.client.SetVolume(int(volume))

    async def _play_impl(self, stream_id: str, chunk: bytes) -> None:
        loop = asyncio.get_running_loop()
        ret = await loop.run_in_executor(None, self.client.PlayStream, self.app_name, stream_id, chunk)
        code = ret[0] if isinstance(ret, tuple) else ret
        if code != 0:
            print(f"[speaker] PlayStream 오류 코드 {code}")

    async def _stop_impl(self) -> None:
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self.client.PlayStop, self.app_name)


class FileSpeaker(BaseSpeaker):
    """재생 시각을 반영해 출력 음성을 WAV로 저장하는 시험용 출력."""

    def __init__(self, path: str, **kw):
        super().__init__(**kw)
        self.path = path
        self.t0 = time.monotonic()
        self.segments: list[tuple[float, bytes]] = []

    async def _play_impl(self, stream_id: str, chunk: bytes) -> None:
        start = max(self.play_end, time.monotonic())
        self.segments.append((start - self.t0, chunk))

    async def _stop_impl(self) -> None:
        pass

    def save(self) -> None:
        if not self.segments:
            write_wav(self.path, b"")
            return
        total = max(t + pcm_duration_s(c) for t, c in self.segments)
        out = bytearray(int(total * ROBOT_RATE + 1) * BYTES_PER_SAMPLE)
        for t, c in self.segments:
            off = int(t * ROBOT_RATE) * BYTES_PER_SAMPLE
            out[off: off + len(c)] = c
        write_wav(self.path, bytes(out))


def make_speaker(spec: str, **kw) -> BaseSpeaker:
    """'robot' 또는 'file:경로.wav' 형식의 지정 문자열로 출력 장치를 만든다."""
    if spec == "robot":
        return RobotSpeaker(**kw)
    if spec.startswith("file:"):
        kw.pop("volume", None)
        kw.pop("app_name", None)
        return FileSpeaker(spec[5:], **kw)
    raise SystemExit(f"알 수 없는 --speaker 값: {spec}")
