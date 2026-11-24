#!/usr/bin/env python3
"""점검 2단계: 로봇 스피커 출력 확인.

WAV 파일(임의 샘플링 주파수·채널, 16비트)을 16 kHz 모노로 바꿔 PlayStream으로 재생한다.
WAV를 지정하지 않으면 440 Hz 시험음을 1초 재생한다.

  python3 checks/check_speaker.py --iface enp3s0
  python3 checks/check_speaker.py --iface enp3s0 --wav samples/korean_tts.wav --volume 70
"""
import argparse
import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from g1voice.audio_utils import make_tone, pcm_duration_s, read_wav_as_robot_pcm  # noqa: E402
from g1voice.robot_speaker import RobotSpeaker  # noqa: E402


async def main(args):
    from unitree_sdk2py.core.channel import ChannelFactoryInitialize
    ChannelFactoryInitialize(0, args.iface)

    pcm = read_wav_as_robot_pcm(args.wav) if args.wav else make_tone(440, 1.0)
    print(f"[check_speaker] 재생 길이 {pcm_duration_s(pcm):.2f} s")
    spk = RobotSpeaker(app_name="check_speaker", volume=args.volume)
    spk.start()
    spk.begin_response()
    spk.write(pcm)
    spk.end_response()
    while spk.busy():
        await asyncio.sleep(0.1)
    await spk.close()
    print("[check_speaker] 완료. 로봇 스피커에서 소리가 났는지 확인한다.")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--iface", required=True, help="로봇과 연결된 네트워크 카드 이름")
    ap.add_argument("--wav", help="재생할 WAV 파일(16비트)")
    ap.add_argument("--volume", type=int)
    asyncio.run(main(ap.parse_args()))
