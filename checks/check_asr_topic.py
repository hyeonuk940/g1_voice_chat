#!/usr/bin/env python3
"""점검 3단계(보조): 로봇 내장 음성 인식 결과(rt/audio_msg) 구독.

로봇 근처에서 한국어로 말해 보고, 내장 음성 인식이 한국어를 인식하는지 확인한다.
메시지는 JSON 문자열이며 text, angle, confidence, language 등의 필드를 포함한다.

  python3 checks/check_asr_topic.py --iface enp3s0 --seconds 30
"""
import argparse
import json
import time

from unitree_sdk2py.core.channel import ChannelFactoryInitialize, ChannelSubscriber
from unitree_sdk2py.idl.std_msgs.msg.dds_ import String_


def handler(msg: String_):
    try:
        d = json.loads(msg.data)
        print(f"[asr] text={d.get('text')!r} lang={d.get('language')} "
              f"conf={d.get('confidence')} angle={d.get('angle')}")
    except (ValueError, TypeError):
        print(f"[asr] {msg.data}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--iface", required=True)
    ap.add_argument("--seconds", type=float, default=30.0)
    args = ap.parse_args()
    ChannelFactoryInitialize(0, args.iface)
    sub = ChannelSubscriber("rt/audio_msg", String_)
    sub.Init(handler, 10)
    print(f"[asr] rt/audio_msg 구독 중 ({args.seconds:.0f} s). 로봇 근처에서 말해 본다.")
    time.sleep(args.seconds)
