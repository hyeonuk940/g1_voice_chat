from .base import (AssistantText, AudioChunk, ErrorEvent, Interrupted, ResponseDone,
                   ResponseStarted, SpeechStarted, UserText, VoiceBackend)


def make_backend(name: str, **kw) -> VoiceBackend:
    """백엔드 이름으로 객체를 만든다. 필요한 SDK는 선택한 백엔드만 import한다."""
    if name == "openai":
        from .openai_realtime import OpenAIRealtimeBackend
        kw.pop("vad_rms", None)
        return OpenAIRealtimeBackend(**kw)
    if name == "gemini":
        from .gemini_live import GeminiLiveBackend
        kw.pop("vad_rms", None)
        kw.pop("transcribe_model", None)
        kw.pop("language", None)
        kw.pop("turn_detection", None)
        return GeminiLiveBackend(**kw)
    if name == "echo":
        from .echo import EchoBackend
        return EchoBackend(**kw)
    raise SystemExit(f"알 수 없는 백엔드: {name}")


__all__ = ["make_backend", "VoiceBackend", "ResponseStarted", "AudioChunk", "ResponseDone",
           "UserText", "AssistantText", "SpeechStarted", "Interrupted", "ErrorEvent"]
