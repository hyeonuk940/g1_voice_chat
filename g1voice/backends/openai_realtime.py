"""OpenAI Realtime API 백엔드 (WebSocket, 음성 입력 → 음성 출력).

- 연결: wss://api.openai.com/v1/realtime?model=<모델>, 헤더 Authorization: Bearer <키>
- 입력: input_audio_buffer.append (base64, 24 kHz PCM) → 로봇 16 kHz를 24 kHz로 올려 보낸다.
- 출력: response.output_audio.delta (base64, 24 kHz PCM) → 16 kHz로 내려 로봇에 보낸다.
- 발화 구간 판단: 서버 VAD(server_vad 또는 semantic_vad)가 발화 종료를 판단하고 응답을 자동 생성한다.
"""
from __future__ import annotations

import base64
import json
import os
from typing import AsyncIterator, Optional

from ..audio_utils import ROBOT_RATE, StreamResampler
from .base import (AssistantText, AudioChunk, ErrorEvent, ResponseDone, ResponseStarted,
                   SpeechStarted, UserText, VoiceBackend)

API_RATE = 24000
URL = "wss://api.openai.com/v1/realtime?model={model}"


class OpenAIRealtimeBackend(VoiceBackend):
    name = "openai"

    def __init__(self, model: str = "gpt-realtime-2.1-mini", instructions: str = "",
                 voice: str = "marin", transcribe_model: Optional[str] = "gpt-transcribe",
                 language: str = "ko", turn_detection: str = "server_vad",
                 silence_ms: int = 500, api_key: Optional[str] = None,
                 url: Optional[str] = None):
        super().__init__(model, instructions)
        self.url = url or os.environ.get("OPENAI_REALTIME_URL") or URL.format(model=model)
        self.voice = voice
        self.transcribe_model = transcribe_model
        self.language = language
        self.turn_detection = turn_detection
        self.silence_ms = silence_ms
        self.api_key = api_key or os.environ.get("OPENAI_API_KEY")
        if not self.api_key:
            raise SystemExit("OPENAI_API_KEY 환경 변수를 설정해야 한다.")
        self.ws = None
        self._up = StreamResampler(ROBOT_RATE, API_RATE)
        self._down = StreamResampler(API_RATE, ROBOT_RATE)
        self._out_text = ""

    def _session_config(self) -> dict:
        if self.turn_detection == "semantic_vad":
            td = {"type": "semantic_vad"}
        else:
            td = {"type": "server_vad", "silence_duration_ms": self.silence_ms}
        audio_in: dict = {"format": {"type": "audio/pcm", "rate": API_RATE}, "turn_detection": td}
        if self.transcribe_model:
            audio_in["transcription"] = {"model": self.transcribe_model, "language": self.language}
        return {
            "type": "session.update",
            "session": {
                "type": "realtime",
                "instructions": self.instructions,
                "audio": {
                    "input": audio_in,
                    "output": {"format": {"type": "audio/pcm", "rate": API_RATE}, "voice": self.voice},
                },
            },
        }

    async def connect(self) -> None:
        from websockets.asyncio.client import connect

        self.ws = await connect(
            self.url,
            additional_headers={"Authorization": f"Bearer {self.api_key}"},
            max_size=None,
        )
        await self.ws.send(json.dumps(self._session_config()))

    async def send_audio(self, pcm16k: bytes) -> None:
        pcm24k = self._up.process(pcm16k)
        if pcm24k:
            await self.ws.send(json.dumps({
                "type": "input_audio_buffer.append",
                "audio": base64.b64encode(pcm24k).decode("ascii"),
            }))

    async def events(self) -> AsyncIterator[object]:
        async for raw in self.ws:
            ev = json.loads(raw)
            t = ev.get("type", "")
            if t == "response.created":
                self._down.reset()
                self._out_text = ""
                yield ResponseStarted()
            elif t in ("response.output_audio.delta", "response.audio.delta"):
                pcm = self._down.process(base64.b64decode(ev.get("delta", "")))
                if pcm:
                    yield AudioChunk(pcm)
            elif t in ("response.output_audio_transcript.delta", "response.audio_transcript.delta"):
                self._out_text += ev.get("delta", "")
            elif t in ("response.output_audio_transcript.done", "response.audio_transcript.done"):
                yield AssistantText(ev.get("transcript") or self._out_text)
                self._out_text = ""
            elif t == "conversation.item.input_audio_transcription.completed":
                yield UserText(ev.get("transcript", ""))
            elif t == "input_audio_buffer.speech_started":
                yield SpeechStarted()
            elif t == "response.done":
                tail = self._down.flush()
                if tail:
                    yield AudioChunk(tail)
                status = (ev.get("response") or {}).get("status", "completed")
                yield ResponseDone(status)
            elif t == "error":
                yield ErrorEvent(json.dumps(ev.get("error", ev), ensure_ascii=False))
            elif t in ("session.created", "session.updated"):
                print(f"[openai] {t}")

    async def close(self) -> None:
        if self.ws is not None:
            await self.ws.close()
