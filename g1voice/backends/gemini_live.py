"""Google Gemini Live API 백엔드 (google-genai SDK, 음성 입력 → 음성 출력).

- 입력: 16 kHz 16비트 PCM(audio/pcm;rate=16000) → 로봇 마이크 형식과 같아 변환이 필요 없다.
- 출력: 24 kHz 16비트 PCM → 16 kHz로 내려 로봇에 보낸다.
- 발화 구간 판단: 서버의 자동 음성 활동 감지(automatic activity detection)를 쓴다.
- receive()는 한 턴이 끝나면 반복이 끝나므로 바깥에서 다시 호출한다.
"""
from __future__ import annotations

import contextlib
import os
from typing import AsyncIterator, Optional

from ..audio_utils import ROBOT_RATE, StreamResampler
from .base import (AssistantText, AudioChunk, ErrorEvent, Interrupted, ResponseDone,
                   ResponseStarted, UserText, VoiceBackend)

API_OUT_RATE = 24000


class GeminiLiveBackend(VoiceBackend):
    name = "gemini"

    def __init__(self, model: str = "gemini-3.8-live", instructions: str = "",
                 voice: str = "Kore", silence_ms: int = 500, api_key: Optional[str] = None):
        super().__init__(model, instructions)
        self.voice = voice
        self.silence_ms = silence_ms
        self.api_key = api_key or os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
        if not self.api_key:
            raise SystemExit("GEMINI_API_KEY(또는 GOOGLE_API_KEY) 환경 변수를 설정해야 한다.")
        self._stack = contextlib.AsyncExitStack()
        self.session = None
        self._down = StreamResampler(API_OUT_RATE, ROBOT_RATE)

    def _config(self):
        from google.genai import types

        return types.LiveConnectConfig(
            response_modalities=["AUDIO"],
            speech_config=types.SpeechConfig(
                voice_config=types.VoiceConfig(
                    prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=self.voice))),
            system_instruction=types.Content(parts=[types.Part(text=self.instructions)]),
            input_audio_transcription=types.AudioTranscriptionConfig(),
            output_audio_transcription=types.AudioTranscriptionConfig(),
            realtime_input_config=types.RealtimeInputConfig(
                automatic_activity_detection=types.AutomaticActivityDetection(
                    disabled=False, silence_duration_ms=self.silence_ms)),
        )

    async def connect(self) -> None:
        from google import genai

        client = genai.Client(api_key=self.api_key, http_options={"api_version": "v1beta"})
        self.session = await self._stack.enter_async_context(
            client.aio.live.connect(model=self.model, config=self._config()))
        print("[gemini] 세션 연결됨")

    async def send_audio(self, pcm16k: bytes) -> None:
        from google.genai import types

        await self.session.send_realtime_input(
            audio=types.Blob(data=pcm16k, mime_type=f"audio/pcm;rate={ROBOT_RATE}"))

    async def events(self) -> AsyncIterator[object]:
        in_turn = False
        user_text, bot_text = "", ""
        while True:
            got_any = False
            async for msg in self.session.receive():
                got_any = True
                if msg.go_away is not None:
                    print(f"[gemini] 서버가 곧 세션을 종료한다: {msg.go_away}")
                sc = msg.server_content
                if sc is None:
                    continue
                if sc.input_transcription and sc.input_transcription.text:
                    user_text += sc.input_transcription.text
                if sc.output_transcription and sc.output_transcription.text:
                    bot_text += sc.output_transcription.text
                if sc.model_turn and sc.model_turn.parts:
                    for part in sc.model_turn.parts:
                        data = part.inline_data.data if part.inline_data else None
                        if not data:
                            continue
                        if not in_turn:
                            in_turn = True
                            self._down.reset()
                            yield ResponseStarted()
                        pcm = self._down.process(data)
                        if pcm:
                            yield AudioChunk(pcm)
                if sc.interrupted:
                    yield Interrupted()
                if sc.turn_complete:
                    tail = self._down.flush()
                    if tail:
                        yield AudioChunk(tail)
                    if user_text.strip():
                        yield UserText(user_text.strip())
                    if bot_text.strip():
                        yield AssistantText(bot_text.strip())
                    if in_turn:
                        yield ResponseDone("interrupted" if sc.interrupted else "completed")
                    in_turn = False
                    user_text, bot_text = "", ""
            if not got_any:
                yield ErrorEvent("Gemini 세션 수신이 끝났다(연결 종료).")
                return

    async def close(self) -> None:
        await self._stack.aclose()
