#!/usr/bin/env python3
"""점검 3단계: 로봇 마이크 멀티캐스트 수신 확인.

지정한 시간 동안 마이크 스트림을 받아 WAV로 저장하고, 초당 수신량과 음량(RMS)을 출력한다.
정상이라면 초당 약 32,000바이트(16 kHz × 2바이트)가 들어온다.
수신량이 0이면 로봇의 깨우기 모드, --local-ip, 방화벽(UDP 5555)을 확인한다.

  python3 checks/check_mic.py --local-ip 192.168.123.99 --seconds 5 --out logs/mic_test.wav
"""
import argparse
import asyncio
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from g1voice.audio_utils import pcm_rms, write_wav  # noqa: E402
from g1voice.robot_mic import RobotMic  # noqa: E402


async def main(args):
    mic = RobotMic(local_ip=args.local_ip, frame_ms=100)
    buf = bytearray()
    t0 = time.monotonic()
    last_report = t0
    sec_bytes = 0

    async def collect():
        nonlocal last_report, sec_bytes
        async for fr in mic.frames():
            buf.extend(fr)
            sec_bytes += len(fr)
            now = time.monotonic()
            if now - last_report >= 1.0:
                print(f"[check_mic] {now - t0:4.1f} s  수신 {sec_bytes:6d} B/s  "
                      f"패킷 누계 {mic.packets}  RMS {pcm_rms(fr):7.1f}")
                last_report, sec_bytes = now, 0

    try:
        await asyncio.wait_for(collect(), timeout=args.seconds)
    except asyncio.TimeoutError:
        pass
    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    write_wav(args.out, bytes(buf))
    print(f"[check_mic] 총 {len(buf)} B ({len(buf) / 32000:.2f} s 분량) 저장: {args.out}")
    if not buf:
        print("[check_mic] 수신 없음: 깨우기 모드, --local-ip, 방화벽(UDP 5555)을 확인한다.")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--local-ip", required=True, help="이 PC의 192.168.123.x 주소")
    ap.add_argument("--seconds", type=float, default=5.0)
    ap.add_argument("--out", default="logs/mic_test.wav")
    asyncio.run(main(ap.parse_args()))
