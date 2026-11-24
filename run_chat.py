#!/usr/bin/env python3
"""G1 음성 대화 실행 스크립트.

구성: 로봇 마이크(UDP 멀티캐스트) → 음성 모델(OpenAI Realtime / Gemini Live) → 로봇 스피커(PlayStream)

실행 예
  # 실기: OpenAI
  python3 run_chat.py --backend openai --iface enp3s0 --local-ip 192.168.123.99
  # 실기: Gemini
  python3 run_chat.py --backend gemini --iface enp3s0 --local-ip 192.168.123.99
  # 로봇 없이 시험: WAV 입력 → 응답을 WAV로 저장
  python3 run_chat.py --backend openai --mic file:samples/q1.wav --speaker file:out/q1_openai.wav --duration 25
  # API 없이 흐름만 시험(메아리 백엔드)
  python3 run_chat.py --backend echo --mic file:samples/q1.wav --speaker file:out/echo.wav --duration 15
"""
from __future__ import annotations

import argparse
import asyncio
import os
import time

from g1voice.audio_utils import pcm_rms
from g1voice.backends import (AssistantText, AudioChunk, ErrorEvent, Interrupted, ResponseDone,
                              ResponseStarted, SpeechStarted, UserText, make_backend)
from g1voice.latency import TurnLogger
from g1voice.robot_mic import make_mic
from g1voice.robot_speaker import FileSpeaker, make_speaker

DEFAULTS = {
    "openai": {"model": "gpt-realtime-2.1-mini", "voice": "marin"},
    "gemini": {"model": "gemini-3.8-live", "voice": "Kore"},
    "echo": {"model": "echo", "voice": ""},
}


def parse_args():
    p = argparse.ArgumentParser(description="G1 음성 대화 (외부 음성 모델 연결)")
    p.add_argument("--backend", choices=["openai", "gemini", "echo"], default="openai")
    p.add_argument("--model", help="모델 이름(기본값은 백엔드별 설정)")
    p.add_argument("--voice", help="응답 음성 이름(기본값은 백엔드별 설정)")
    p.add_argument("--prompt", default=os.path.join(os.path.dirname(__file__), "prompts", "system_ko.txt"))
    p.add_argument("--iface", help="로봇과 연결된 네트워크 카드 이름(DDS용, 예: enp3s0)")
    p.add_argument("--local-ip", help="이 PC의 192.168.123.x 주소(마이크 멀티캐스트 수신용)")
    p.add_argument("--mic", default="robot", help="robot 또는 file:경로.wav")
    p.add_argument("--speaker", default="robot", help="robot 또는 file:경로.wav")
    p.add_argument("--volume", type=int, help="로봇 스피커 음량(0~100)")
    p.add_argument("--silence-ms", type=int, default=500, help="발화 종료로 판단할 무음 길이")
    p.add_argument("--turn-detection", choices=["server_vad", "semantic_vad"], default="server_vad",
                   help="OpenAI 발화 종료 판단 방식")
    p.add_argument("--transcribe-model", default="gpt-transcribe",
                   help="OpenAI 입력 전사 모델(기록용). 'none'이면 끈다")
    p.add_argument("--vad-rms", type=float, default=500.0,
                   help="지연 측정용 발화 판단 음량 문턱값(16비트 PCM RMS)")
    p.add_argument("--tail-s", type=float, default=0.5, help="재생 종료 후 마이크 차단을 유지할 시간")
    p.add_argument("--min-chunk-s", type=float, default=0.3,
                   help="PlayStream 한 번에 보내는 최소 음성 길이(재생이 끊기면 늘린다)")
    p.add_argument("--max-ahead-s", type=float, default=3.0,
                   help="로봇에 미리 보내 둘 수 있는 최대 음성 길이")
    p.add_argument("--no-gate", action="store_true",
                   help="재생 중에도 마이크 입력을 보낸다(끼어들기 시험용, 자기 목소리 인식 위험)")
    p.add_argument("--frame-ms", type=int, default=20)
    p.add_argument("--duration", type=float, help="지정한 초가 지나면 종료(파일 시험용)")
    p.add_argument("--log", help="턴별 지연 기록 CSV 경로")
    return p.parse_args()


async def run(args) -> None:
    d = DEFAULTS[args.backend]
    model = args.model or d["model"]
    voice = args.voice or d["voice"]
    with open(args.prompt, encoding="utf-8") as f:
        instructions = f.read().strip()

    if args.speaker == "robot":
        if not args.iface:
            raise SystemExit("--iface(로봇과 연결된 네트워크 카드 이름)를 지정해야 한다.")
        from unitree_sdk2py.core.channel import ChannelFactoryInitialize
        ChannelFactoryInitialize(0, args.iface)

    speaker = make_speaker(args.speaker, volume=args.volume, tail_s=args.tail_s,
                           min_chunk_s=args.min_chunk_s, max_ahead_s=args.max_ahead_s)
    speaker.start()
    mic = make_mic(args.mic, args.local_ip, frame_ms=args.frame_ms)
    backend = make_backend(
        args.backend, model=model, instructions=instructions, voice=voice,
        silence_ms=args.silence_ms, turn_detection=args.turn_detection,
        transcribe_model=None if args.transcribe_model == "none" else args.transcribe_model,
        language="ko", vad_rms=args.vad_rms,
    )
    log_path = args.log or os.path.join(
        "logs", f"turns_{args.backend}_{time.strftime('%Y%m%d_%H%M%S')}.csv")
    logger = TurnLogger(log_path, args.backend, model, args.vad_rms)
    speaker.on_first_play = logger.on_first_play
    gate = not args.no_gate
    stats = {"sent": 0, "gated": 0}

    await backend.connect()
    print(f"[run] 백엔드={args.backend} 모델={model} 음성={voice} 입력={args.mic} "
          f"출력={args.speaker} 재생 중 입력 차단={'켜짐' if gate else '꺼짐'}")

    async def pump_mic():
        async for fr in mic.frames():
            now = time.monotonic()
            if gate and speaker.busy(now):
                stats["gated"] += 1
                continue
            logger.on_mic_frame(pcm_rms(fr), now)
            await backend.send_audio(fr)
            stats["sent"] += 1

    async def pump_events():
        first_audio = False
        async for ev in backend.events():
            if isinstance(ev, ResponseStarted):
                speaker.begin_response()
                first_audio = False
            elif isinstance(ev, AudioChunk):
                if not first_audio:
                    logger.on_first_audio()
                    first_audio = True
                speaker.write(ev.pcm16k)
            elif isinstance(ev, ResponseDone):
                speaker.end_response()
                logger.end_turn(ev.status)
            elif isinstance(ev, UserText):
                print(f"[사용자] {ev.text}")
                logger.on_user_text(ev.text)
            elif isinstance(ev, AssistantText):
                print(f"[로봇] {ev.text}")
                logger.on_assistant_text(ev.text)
            elif isinstance(ev, Interrupted):
                await speaker.stop()
            elif isinstance(ev, SpeechStarted):
                if not gate:          # 입력 차단을 끈 경우에만 끼어들기로 처리
                    await speaker.stop()
            elif isinstance(ev, ErrorEvent):
                print(f"[오류] {ev.message}")

    async def ticker():
        while True:
            await asyncio.sleep(0.5)
            logger.tick()

    tasks = [asyncio.create_task(c) for c in (pump_mic(), pump_events(), ticker())]
    try:
        # 마이크 입력이 끝나거나(파일 입력) 음성 모델 연결이 끝나면 종료한다.
        await asyncio.wait(tasks, timeout=args.duration, return_when=asyncio.FIRST_COMPLETED)
        for t in tasks:
            if t.done() and not t.cancelled() and t.exception():
                raise t.exception()
    finally:
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await backend.close()
        await speaker.close()
        logger.close()
        if isinstance(speaker, FileSpeaker):
            speaker.save()
            print(f"[run] 출력 음성 저장: {speaker.path}")
        print(f"[run] 보낸 프레임 {stats['sent']}, 재생 중 차단한 프레임 {stats['gated']}, 기록: {log_path}")


def main():
    args = parse_args()
    try:
        asyncio.run(run(args))
    except KeyboardInterrupt:
        print("\n[run] 종료")


if __name__ == "__main__":
    main()
