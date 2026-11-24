"""음성 입력: G1 마이크 멀티캐스트 스트림 수신, 또는 WAV 파일 재생(로봇 없이 시험할 때).

G1 음성 서비스 문서 기준 마이크 원시 스트림 형식
- UDP 멀티캐스트 그룹 239.168.123.161, 포트 5555
- 16 kHz, 모노, 16비트 PCM
- 수신 PC는 192.168.123.x 대역 네트워크 카드로 그룹에 가입해야 한다.
- 로봇의 깨우기 모드(앱 또는 리모컨)가 켜져 있어야 마이크가 동작한다.
"""
from __future__ import annotations

import asyncio
import socket
import struct
import threading
import time
from typing import AsyncIterator, Optional

from .audio_utils import ROBOT_RATE, BYTES_PER_SAMPLE, read_wav_as_robot_pcm, silence

MIC_GROUP = "239.168.123.161"
MIC_PORT = 5555


def frame_bytes(frame_ms: int) -> int:
    return int(ROBOT_RATE * frame_ms / 1000) * BYTES_PER_SAMPLE


class _Reframer:
    """임의 크기 패킷을 고정 크기 프레임으로 다시 자른다."""

    def __init__(self, size: int):
        self.size = size
        self.buf = bytearray()

    def push(self, data: bytes):
        self.buf.extend(data)
        while len(self.buf) >= self.size:
            out = bytes(self.buf[: self.size])
            del self.buf[: self.size]
            yield out


class RobotMic:
    """G1 마이크 멀티캐스트 수신기. frames()가 16 kHz PCM 프레임을 비동기로 내보낸다."""

    def __init__(self, local_ip: str, group: str = MIC_GROUP, port: int = MIC_PORT,
                 frame_ms: int = 20, recv_timeout_s: float = 1.0):
        self.local_ip = local_ip
        self.group = group
        self.port = port
        self.frame_size = frame_bytes(frame_ms)
        self.recv_timeout_s = recv_timeout_s
        self.packets = 0
        self.bytes_received = 0
        self._stop = threading.Event()

    def _open_socket(self) -> socket.socket:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("", self.port))
        mreq = struct.pack("4s4s", socket.inet_aton(self.group), socket.inet_aton(self.local_ip))
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
        sock.settimeout(self.recv_timeout_s)
        return sock

    async def frames(self) -> AsyncIterator[bytes]:
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue = asyncio.Queue(maxsize=500)
        sock = self._open_socket()
        reframer = _Reframer(self.frame_size)

        def reader():
            while not self._stop.is_set():
                try:
                    data, _ = sock.recvfrom(65536)
                except socket.timeout:
                    continue
                except OSError:
                    break
                self.packets += 1
                self.bytes_received += len(data)
                for fr in reframer.push(data):
                    loop.call_soon_threadsafe(_put_drop_oldest, queue, fr)
            sock.close()

        th = threading.Thread(target=reader, name="robot-mic", daemon=True)
        th.start()
        try:
            while True:
                yield await queue.get()
        finally:
            self._stop.set()


def _put_drop_oldest(queue: asyncio.Queue, item: bytes) -> None:
    """큐가 가득 차면 가장 오래된 프레임을 버린다(처리 지연이 쌓이지 않게)."""
    if queue.full():
        try:
            queue.get_nowait()
        except asyncio.QueueEmpty:
            pass
    queue.put_nowait(item)


class FileMic:
    """WAV 파일을 실시간 속도로 흘려보내는 시험용 입력. 로봇 없이 API 연결을 시험할 때 쓴다.

    파일이 끝나면 tail_s 동안 무음을 보낸다. 무음이 있어야 음성 모델의 발화 종료 판단(VAD)이 동작한다.
    """

    def __init__(self, path: str, frame_ms: int = 20, tail_s: float = 15.0,
                 lead_s: float = 1.0):
        self.pcm = silence(lead_s) + read_wav_as_robot_pcm(path) + silence(tail_s)
        self.frame_size = frame_bytes(frame_ms)
        self.frame_s = frame_ms / 1000

    async def frames(self) -> AsyncIterator[bytes]:
        t0 = time.monotonic()
        n = 0
        for off in range(0, len(self.pcm), self.frame_size):
            fr = self.pcm[off: off + self.frame_size]
            if len(fr) < self.frame_size:
                fr = fr + b"\x00" * (self.frame_size - len(fr))
            n += 1
            # 실시간 속도 유지
            delay = t0 + n * self.frame_s - time.monotonic()
            if delay > 0:
                await asyncio.sleep(delay)
            yield fr


def make_mic(spec: str, local_ip: Optional[str], frame_ms: int = 20):
    """'robot' 또는 'file:경로.wav' 형식의 지정 문자열로 입력 장치를 만든다."""
    if spec == "robot":
        if not local_ip:
            raise SystemExit("--local-ip(192.168.123.x 대역의 이 PC 주소)를 지정해야 한다.")
        return RobotMic(local_ip=local_ip, frame_ms=frame_ms)
    if spec.startswith("file:"):
        return FileMic(spec[5:], frame_ms=frame_ms)
    raise SystemExit(f"알 수 없는 --mic 값: {spec}")
