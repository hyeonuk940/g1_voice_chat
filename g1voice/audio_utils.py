"""PCM 변환 유틸리티.

로봇(G1) 쪽 음성 형식은 16 kHz, 모노, 16비트 PCM(리틀 엔디언)이다.
음성 모델 API는 24 kHz 출력을 쓰는 경우가 많으므로 스트리밍 리샘플러를 둔다.
"""
from __future__ import annotations

import wave

import numpy as np

ROBOT_RATE = 16000          # G1 마이크 스트림과 PlayStream 입력의 샘플링 주파수
BYTES_PER_SAMPLE = 2        # 16비트


def pcm_duration_s(pcm: bytes, rate: int = ROBOT_RATE) -> float:
    """16비트 모노 PCM 바이트 길이를 초 단위 재생 시간으로 바꾼다."""
    return len(pcm) / (rate * BYTES_PER_SAMPLE)


def pcm_rms(pcm: bytes) -> float:
    """16비트 PCM의 RMS(음량 지표)를 계산한다. 빈 입력은 0을 반환한다."""
    if len(pcm) < 2:
        return 0.0
    x = np.frombuffer(pcm[: len(pcm) // 2 * 2], dtype="<i2").astype(np.float32)
    return float(np.sqrt(np.mean(x * x)))


class StreamResampler:
    """청크 단위 입력을 끊김 없이 리샘플링한다(soxr 스트리밍 사용).

    같은 객체에 연속 청크를 넣으면 청크 경계에서 잡음이 생기지 않는다.
    한 응답이 끝나면 flush()로 남은 샘플을 꺼내고 reset()으로 새 스트림을 시작한다.
    """

    def __init__(self, in_rate: int, out_rate: int):
        self.in_rate = in_rate
        self.out_rate = out_rate
        self._stream = None
        self.reset()

    def reset(self) -> None:
        if self.in_rate == self.out_rate:
            self._stream = None
            return
        import soxr  # 지연 import: 리샘플링이 필요 없는 경우 의존성 불필요

        self._stream = soxr.ResampleStream(self.in_rate, self.out_rate, 1, dtype="int16")

    def process(self, pcm: bytes) -> bytes:
        if self._stream is None:
            return pcm
        x = np.frombuffer(pcm[: len(pcm) // 2 * 2], dtype="<i2")
        y = self._stream.resample_chunk(x)
        return y.astype("<i2").tobytes()

    def flush(self) -> bytes:
        if self._stream is None:
            return b""
        y = self._stream.resample_chunk(np.zeros(0, dtype="<i2"), last=True)
        self.reset()
        return y.astype("<i2").tobytes()


def read_wav_as_robot_pcm(path: str) -> bytes:
    """임의의 16비트 WAV(모노/스테레오, 임의 샘플링 주파수)를 16 kHz 모노 PCM으로 읽는다."""
    with wave.open(path, "rb") as wf:
        if wf.getsampwidth() != 2:
            raise ValueError(f"16비트 WAV만 지원한다: sampwidth={wf.getsampwidth()}")
        rate = wf.getframerate()
        ch = wf.getnchannels()
        raw = wf.readframes(wf.getnframes())
    x = np.frombuffer(raw, dtype="<i2")
    if ch > 1:
        x = x.reshape(-1, ch).mean(axis=1).astype("<i2")
    pcm = x.tobytes()
    if rate != ROBOT_RATE:
        rs = StreamResampler(rate, ROBOT_RATE)
        pcm = rs.process(pcm) + rs.flush()
    return pcm


def write_wav(path: str, pcm: bytes, rate: int = ROBOT_RATE) -> None:
    """16비트 모노 PCM을 WAV 파일로 저장한다."""
    with wave.open(path, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(BYTES_PER_SAMPLE)
        wf.setframerate(rate)
        wf.writeframes(pcm)


def make_tone(freq_hz: float = 440.0, seconds: float = 1.0, rate: int = ROBOT_RATE,
              amplitude: int = 8000) -> bytes:
    """시험용 사인파 PCM을 만든다."""
    t = np.arange(int(seconds * rate)) / rate
    y = (amplitude * np.sin(2 * np.pi * freq_hz * t)).astype("<i2")
    return y.tobytes()


def silence(seconds: float, rate: int = ROBOT_RATE) -> bytes:
    return b"\x00\x00" * int(seconds * rate)


__all__ = [
    "ROBOT_RATE", "BYTES_PER_SAMPLE", "pcm_duration_s", "pcm_rms", "StreamResampler",
    "read_wav_as_robot_pcm", "write_wav", "make_tone", "silence",
]
