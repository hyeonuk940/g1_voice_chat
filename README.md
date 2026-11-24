# G1 음성 대화 (외부 음성 모델 연결)

Unitree G1의 마이크와 스피커를 외부 음성 모델(OpenAI Realtime, Gemini Live)에 연결해 한국어 음성 대화를 수행하는 코드이다. 로봇 입출력은 공통으로 두고 음성 모델만 교체할 수 있게 구성하여, 두 모델의 한국어 인식 정확도·발음 품질·응답 지연을 같은 조건에서 비교한다.

## 시스템 구성

```
G1 마이크 ──UDP 멀티캐스트(16 kHz PCM)──▶ PC3 run_chat.py ──WebSocket──▶ 음성 모델(OpenAI / Gemini)
G1 스피커 ◀──PlayStream(16 kHz PCM, DDS)── PC3 run_chat.py ◀─────────── 응답 음성(24 kHz → 16 kHz 변환)
```

- 입력: 마이크 원시 스트림(그룹 239.168.123.161, 포트 5555, 16 kHz·모노·16비트)
- 출력: `AudioClient.PlayStream()`, 응답 하나당 stream_id 하나
- 재생 중 입력 차단: 로봇 음성이 다시 마이크로 들어가는 문제를 막기 위해 재생 중과 재생 종료 후 0.5초 동안 마이크 입력을 보내지 않음(반이중 동작)
- 지연 기록: 턴마다 발화 종료 → 첫 응답 음성 수신 시간을 CSV로 저장

## 파일 구성

| 경로 | 내용 |
|---|---|
| `run_chat.py` | 대화 실행 스크립트 |
| `g1voice/robot_mic.py` | 마이크 멀티캐스트 수신, WAV 파일 입력(시험용) |
| `g1voice/robot_speaker.py` | PlayStream 재생, WAV 파일 출력(시험용), 재생 중 입력 차단 판단 |
| `g1voice/audio_utils.py` | 스트리밍 리샘플링(soxr), WAV 입출력 |
| `g1voice/latency.py` | 턴별 지연 측정과 CSV 기록 |
| `g1voice/backends/openai_realtime.py` | OpenAI Realtime 백엔드 |
| `g1voice/backends/gemini_live.py` | Gemini Live 백엔드 |
| `g1voice/backends/echo.py` | API 없이 흐름을 시험하는 메아리 백엔드 |
| `checks/check_speaker.py` | 점검: 로봇 스피커 재생 |
| `checks/check_mic.py` | 점검: 로봇 마이크 수신 |
| `checks/check_asr_topic.py` | 점검: 로봇 내장 음성 인식(rt/audio_msg) 한국어 인식 여부 |
| `prompts/system_ko.txt` | 로봇 응답 규칙(시스템 지시문) |

## 준비 사항

**로봇**
- 음성 서비스 버전: Vui_Service 2.0.3.8 이상, Vui Module 2.0.0.3 이상(G1 음성 서비스 문서 기준)
- 깨우기 모드 켜기(앱 또는 리모컨). 꺼져 있으면 마이크 스트림이 나오지 않음

**PC3**
- 네트워크: 로봇은 유선(예: 192.168.123.99/24), 인터넷은 WiFi
- `unitree_sdk2_python` 설치(공식 README 절차, cyclonedds 0.10.2)
- 나머지 패키지: `pip install -r requirements.txt`
- API 키 환경 변수

```bash
export OPENAI_API_KEY=sk-...
export GEMINI_API_KEY=...
```

## 실행 순서

**1. 스피커 점검**
```bash
python3 checks/check_speaker.py --iface enp3s0                     # 440 Hz 시험음
python3 checks/check_speaker.py --iface enp3s0 --wav sample.wav    # 임의 WAV(자동 16 kHz 변환)
```

**2. 마이크 점검**
```bash
python3 checks/check_mic.py --local-ip 192.168.123.99 --seconds 5 --out logs/mic_test.wav
```
- 정상 수신량: 초당 약 32,000바이트
- 수신이 없으면 깨우기 모드, `--local-ip`, 방화벽(UDP 5555) 확인

**3. 내장 음성 인식 점검(선택)**
```bash
python3 checks/check_asr_topic.py --iface enp3s0 --seconds 30
```

**4. 대화 실행**
```bash
python3 run_chat.py --backend openai --iface enp3s0 --local-ip 192.168.123.99
python3 run_chat.py --backend gemini --iface enp3s0 --local-ip 192.168.123.99
```

**5. 로봇 없이 시험(선택)**
```bash
# 녹음한 질문 WAV → 응답을 WAV로 저장
python3 run_chat.py --backend openai --mic file:q1.wav --speaker file:out/q1_openai.wav
python3 run_chat.py --backend gemini --mic file:q1.wav --speaker file:out/q1_gemini.wav
# API 없이 흐름만 확인
python3 run_chat.py --backend echo --mic file:q1.wav --speaker file:out/q1_echo.wav
```
같은 WAV를 두 백엔드에 넣으면 입력 조건이 같으므로 비교 실험에 쓸 수 있다.

## 주요 옵션

| 옵션 | 기본값 | 설명 |
|---|---|---|
| `--backend` | openai | openai, gemini, echo |
| `--model` | gpt-realtime-2.1-mini / gemini-3.8-live | 모델 이름 |
| `--voice` | marin / Kore | 응답 음성 |
| `--silence-ms` | 500 | 발화 종료 판단 무음 길이 |
| `--turn-detection` | server_vad | OpenAI 발화 종료 판단 방식(server_vad, semantic_vad) |
| `--transcribe-model` | gpt-transcribe | OpenAI 입력 전사 모델(기록용), none이면 끔 |
| `--vad-rms` | 500 | 지연 측정용 발화 판단 음량 문턱값 |
| `--tail-s` | 0.5 | 재생 종료 후 입력 차단 유지 시간 |
| `--min-chunk-s` | 0.3 | PlayStream 한 번에 보내는 최소 길이 |
| `--max-ahead-s` | 3.0 | 로봇에 미리 보내는 최대 길이 |
| `--no-gate` | 꺼짐 | 재생 중 입력 허용(끼어들기 시험, 자기 음성 인식 위험) |
| `--volume` | 변경 없음 | 로봇 스피커 음량(0~100) |

## 기록 형식

`logs/turns_<백엔드>_<시각>.csv`

| 열 | 의미 |
|---|---|
| `model_latency_s` | 발화 종료(마이크 음량이 문턱값 아래로 내려간 시점) → 첫 응답 음성 수신 |
| `play_latency_s` | 발화 종료 → 첫 PlayStream 호출 |
| `user_text`, `assistant_text` | 입력·응답 전사 |
| `status` | completed, cancelled, interrupted 등 |

- 두 지연 값에는 음성 모델의 발화 종료 대기 시간(`--silence-ms`)이 포함됨
- 로봇 스피커에서 실제 소리가 나기까지의 추가 지연은 포함되지 않음. 측정이 필요하면 외부 마이크로 사용자 음성과 로봇 음성을 함께 녹음해 파형에서 측정

## 검증 범위

개발 환경에서 수행한 검증
- 리샘플링: 16 kHz → 24 kHz → 16 kHz 왕복 시 길이 비율 1.5, 440 Hz 유지, 상대 오차 0.01%
- 마이크 수신: 루프백 멀티캐스트로 1,000바이트 패킷 32개 송신 → 32,000바이트 전량 수신 및 20 ms 프레임 재구성
- 스피커: 가짜 AudioClient로 8초 응답 재생 시 stream_id 1개 유지, 0.3초 청크, 실시간 대비 최대 3.3초 선행 전송, stop 시 PlayStop 호출
- OpenAI 백엔드: 로컬 모의 서버로 session.update 형식, 24 kHz 입력 변환, 응답 음성 16 kHz 변환(16,000샘플), 전사 이벤트 처리 확인
- Gemini 백엔드: SDK 메시지 객체로 응답 음성 변환(16,000샘플), 입력·출력 전사 누적, 턴 종료 처리 확인
- 전체 흐름: 메아리 백엔드로 3턴 대화, 재생 중 차단 프레임 328개(재생 5.0초 + 차단 유지 1.5초와 일치), 턴별 지연 0.80초(무음 대기 0.5초 + 모의 지연 0.3초와 일치)

실기에서 확인이 필요한 항목
- 실제 API 연결(모의 서버와 실제 서버의 이벤트 차이 여부)
- 0.3초 청크 연속 재생 시 끊김 여부. 끊기면 `--min-chunk-s`를 늘림(공식 예제는 3초 청크 사용)
- 로봇 마이크 멀티캐스트 패킷 형식(16 kHz 모노 16비트 PCM 가정)
- 한국어 인식 정확도와 발음 품질

## 제약 사항

- 끼어들기 미지원: 기본 설정에서는 로봇이 말하는 동안 사용자 음성을 받지 않음
- 세션 시간 제한: 음성 모델 API의 세션 길이 제한으로 장시간 실행 시 연결이 끊길 수 있음. 현재는 다시 실행하는 방식이며, 세션 재개 기능은 추가 구현 대상
- 모델 이름: 2026년 11월 기준 문서의 이름을 기본값으로 사용함. 모델 교체·종료 시 `--model`로 지정
