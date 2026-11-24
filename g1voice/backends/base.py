"""음성 모델 백엔드 공통 인터페이스.

모든 백엔드는 16 kHz 모노 16비트 PCM을 받고, 응답 음성도 16 kHz로 바꿔 내보낸다.
샘플링 주파수 변환은 각 백엔드 안에서 처리하므로 실행 스크립트는 API 차이를 알 필요가 없다.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import AsyncIterator


@dataclass
class ResponseStarted:
    pass


@dataclass
class AudioChunk:
    pcm16k: bytes


@dataclass
class ResponseDone:
    status: str = "completed"


@dataclass
class UserText:
    text: str


@dataclass
class AssistantText:
    text: str


@dataclass
class SpeechStarted:
    """서버 쪽 발화 감지(끼어들기 판단용)."""


@dataclass
class Interrupted:
    """서버가 응답 생성을 중단했다는 알림. 재생 중인 음성을 멈춰야 한다."""


@dataclass
class ErrorEvent:
    message: str


class VoiceBackend:
    name = "base"

    def __init__(self, model: str, instructions: str):
        self.model = model
        self.instructions = instructions

    async def connect(self) -> None:
        raise NotImplementedError

    async def send_audio(self, pcm16k: bytes) -> None:
        raise NotImplementedError

    def events(self) -> AsyncIterator[object]:
        raise NotImplementedError

    async def close(self) -> None:
        pass
