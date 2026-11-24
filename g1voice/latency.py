"""대화 한 턴의 지연 시간 측정과 CSV 기록.

측정 기준(모든 시각은 이 PC의 time.monotonic)
- t_voice_end : 마지막으로 마이크 음량이 문턱값을 넘은 시각(사용자 발화 종료 추정)
- t_first_audio : 음성 모델의 첫 응답 음성이 이 PC에 도착한 시각
- t_first_play : 첫 응답 청크를 로봇 PlayStream에 넘긴 시각

기록 항목
- model_latency_s = t_first_audio - t_voice_end (발화 종료 → 첫 응답 음성 수신)
- play_latency_s  = t_first_play  - t_voice_end (발화 종료 → 로봇 재생 명령)
로봇 스피커에서 실제 소리가 나기까지의 추가 지연은 이 값에 포함되지 않는다.
"""
from __future__ import annotations

import csv
import os
import time
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Turn:
    index: int
    t_voice_end: Optional[float] = None
    t_first_audio: Optional[float] = None
    t_first_play: Optional[float] = None
    user_text: str = ""
    assistant_text: str = ""
    status: str = ""
    extra: dict = field(default_factory=dict)


class TurnLogger:
    FIELDS = ["turn", "backend", "model", "model_latency_s", "play_latency_s",
              "user_text", "assistant_text", "status", "wall_time"]

    def __init__(self, path: str, backend: str, model: str, vad_rms: float):
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        self.path = path
        self.backend = backend
        self.model = model
        self.vad_rms = vad_rms
        self.last_voice_ts: Optional[float] = None
        self.turn_index = 0
        self.current: Optional[Turn] = None
        self.pending: Optional[Turn] = None   # 끝났지만 늦게 오는 입력 전사를 기다리는 턴
        self._pending_since = 0.0
        self._fh = open(path, "w", newline="", encoding="utf-8-sig")  # 엑셀에서 한글이 깨지지 않게 BOM 포함
        self._w = csv.DictWriter(self._fh, fieldnames=self.FIELDS)
        self._w.writeheader()

    # 마이크 프레임마다 호출
    def on_mic_frame(self, rms: float, now: Optional[float] = None) -> None:
        if rms >= self.vad_rms:
            self.last_voice_ts = time.monotonic() if now is None else now

    def _ensure_turn(self) -> Turn:
        if self.current is None:
            self._flush_pending()
            self.turn_index += 1
            self.current = Turn(index=self.turn_index, t_voice_end=self.last_voice_ts)
        return self.current

    def on_first_audio(self, now: Optional[float] = None) -> None:
        t = self._ensure_turn()
        if t.t_first_audio is None:
            t.t_first_audio = time.monotonic() if now is None else now

    def on_first_play(self, ts: float) -> None:
        if self.current and self.current.t_first_play is None:
            self.current.t_first_play = ts

    def on_user_text(self, text: str) -> None:
        # 입력 전사 결과는 응답이 끝난 뒤에 도착하기도 한다.
        # 진행 중인 턴이 없고 직전 턴의 입력 전사가 비어 있으면 직전 턴에 붙인다.
        if self.current is None and self.pending is not None and not self.pending.user_text:
            t = self.pending
        else:
            t = self._ensure_turn()
        t.user_text = (t.user_text + " " + text).strip()

    def on_assistant_text(self, text: str) -> None:
        t = self._ensure_turn()
        t.assistant_text = (t.assistant_text + " " + text).strip()

    def end_turn(self, status: str = "done") -> Optional[Turn]:
        t = self.current
        if t is None:
            return None
        t.status = status
        self._flush_pending()
        self.pending = t
        self._pending_since = time.monotonic()
        self.current = None
        return t

    def tick(self, grace_s: float = 3.0) -> None:
        """주기적으로 호출한다. 끝난 턴을 grace_s 동안 기다린 뒤 기록한다."""
        if self.pending is not None and time.monotonic() - self._pending_since > grace_s:
            self._flush_pending()

    def _flush_pending(self) -> None:
        t = self.pending
        if t is None:
            return

        def diff(a, b):
            return round(a - b, 3) if (a is not None and b is not None) else ""

        row = {
            "turn": t.index, "backend": self.backend, "model": self.model,
            "model_latency_s": diff(t.t_first_audio, t.t_voice_end),
            "play_latency_s": diff(t.t_first_play, t.t_voice_end),
            "user_text": t.user_text, "assistant_text": t.assistant_text,
            "status": t.status, "wall_time": time.strftime("%Y-%m-%d %H:%M:%S"),
        }
        self._w.writerow(row)
        self._fh.flush()
        print(f"[turn {t.index}] 응답 지연 {row['model_latency_s']} s / 재생 명령 지연 "
              f"{row['play_latency_s']} s | 사용자: {t.user_text} | 로봇: {t.assistant_text}")
        self.pending = None

    def close(self) -> None:
        if self.current is not None:
            self.end_turn("closed")
        self._flush_pending()
        self._fh.close()
