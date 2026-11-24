"""API 없이 전체 흐름을 시험하기 위한 메아리 백엔드.

사용자 발화를 음량 기준으로 잘라 두었다가, 발화가 끝나면(무음이 silence_ms 이상 이어지면)
그 음성을 그대로 응답으로 돌려준다. 로봇 마이크 수신, 재생, 재생 중 입력 차단,
지연 기록이 제대로 동작하는지 API 비용 없이 확인할 수 있다.
"""
from __future__ import annotations

import asyncio
from typing import AsyncIterator

from ..audio_utils import ROBOT_RATE, BYTES_PER_SAMPLE, pcm_rms
from .base import (AssistantText, AudioChunk, ResponseDone, ResponseStarted, UserText,
                   VoiceBackend)


class EchoBackend(VoiceBackend):
    name = "echo"

    def __init__(self, vad_rms: float = 500.0, silence_ms: int = 600, delay_s: float = 0.3, **_):
        super().__init__(model="echo", instructions="")
        self.vad_rms = vad_rms
        self.silence_ms = silence_ms
        self.delay_s = delay_s
        self._utt = bytearray()
        self._silence_bytes = 0
        self._speaking = False
        self._queue: asyncio.Queue = asyncio.Queue()

    async def connect(self) -> None:
        print("[echo] 메아리 백엔드 시작")

    async def send_audio(self, pcm16k: bytes) -> None:
        if pcm_rms(pcm16k) >= self.vad_rms:
            self._speaking = True
            self._silence_bytes = 0
            self._utt.extend(pcm16k)
        elif self._speaking:
            self._utt.extend(pcm16k)
            self._silence_bytes += len(pcm16k)
            if self._silence_bytes >= self.silence_ms * ROBOT_RATE * BYTES_PER_SAMPLE // 1000:
                self._queue.put_nowait(bytes(self._utt))
                self._utt.clear()
                self._speaking = False
                self._silence_bytes = 0

    async def events(self) -> AsyncIterator[object]:
        n = 0
        while True:
            utt = await self._queue.get()
            n += 1
            await asyncio.sleep(self.delay_s)          # 모델 응답 지연 흉내
            yield ResponseStarted()
            step = int(0.1 * ROBOT_RATE) * BYTES_PER_SAMPLE
            for off in range(0, len(utt), step):     # 실제 API처럼 조각으로 나눠 보낸다
                yield AudioChunk(utt[off: off + step])
                await asyncio.sleep(0.01)
            yield UserText(f"(발화 {n})")
            yield AssistantText(f"(메아리 {n}, {len(utt) / (ROBOT_RATE * BYTES_PER_SAMPLE):.2f}초)")
            yield ResponseDone("completed")
