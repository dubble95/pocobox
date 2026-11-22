# PocoBox

**PocoBox**는 쓰지 않는 중고 스마트폰, 싱글보드 컴퓨터(Raspberry Pi 등), 또는 저전력 미니 PC를 상시 대기형 전용 스마트 오디오 플레이어 겸 한/영 음성 어시스턴트로 업사이클링하는 오픈소스 소프트웨어 스택입니다.

**Xiaomi POCO F1 (`xiaomi-beryllium`)**을 기준 하드웨어(Reference Device)로 개발되었으나, **핵심 파이프라인 전체가 표준 리눅스(postmarketOS, Debian, Ubuntu, Raspberry Pi OS, Alpine) 기반으로 모듈화**되어 있어 하드웨어 제약 없이 손쉽게 포팅하고 확장할 수 있습니다.

- **Lyrion Music Server (LMS · TIDAL / YouTube / 로컬 음원)** 무중단 호스트 스트리밍
- **외부 USB DAC 핫플러그 감지 시 100% Bit-perfect ALSA 직결 & 자동 이어재생**
- **로컬 우선 하이브리드 음성 명령 (SenseVoice-Small INT8 STT + 규칙 Fast-Path + 클라우드 LLM 폴백)**
- **하드웨어 볼륨 및 오디오 경로 물리적 격리 (음악 재생은 USB DAC 100% 고정, 차임/TTS는 내장 스피커 분리)**
- **풀스크린 반응형 Now-Playing 키오스크 UI 및 실시간 VAD/AEC 엔지니어링 대시보드**

---

## 구현 현황 (All Phases Complete)

- [x] **Phase 0: 리눅스 설치 및 기본 환경 구축** ([01-linux-installation.md](docs/01-linux-installation.md))
  - POCO F1 (`beryllium_global`, `EBBG` 패널) 부트로더 공식 언락 완료
  - postmarketOS (Nura `v26.06` · Phosh · `xiaomi-beryllium-ebbg`) 플래싱 및 부팅 완료
  - 호스트네임 `pocobox` (`pocobox.local` mDNS), Wi-Fi(2.4G/5G), Mac 비밀번호 없는 SSH(`ssh pocobox`) 완료
- [x] **Phase 1: LMS (Docker) + TIDAL 설정/이관 + Squeezelite 로컬 오디오 출력**
  - Lyrion Music Server 9.1.2 (`pocobox-lms`, `http://pocobox.local:9000/material/`) 구동 완료
  - 기존 LMS 설정(`TIDAL`, `MaterialSkin`, `YouTube`, `Favorites` 등) 무중단 이관 및 고유 `server_uuid` 분리
  - Squeezelite `2.0.0-1612` 네이티브 arm64 빌드 (ALSA + DSD + SoXR + Opus + SSL, 고정 MAC `02:70:6f:63:6f:01`)
  - LMS/TIDAL 스마트 검색 JSON-RPC CLI (`pocobox-lms`) 구현 및 실서버 검증
- [x] **Phase 2: USB-C Host 모드 활성화 + USB DAC 연결 감지 시 자동 전환 & 이어재생**
  - 원본 `boot.img` 이중 백업 + Device Tree `dr_mode = "host"` 패치 및 `mkinitfs` 반영 (`lsusb` xHCI 2.0/3.0 확인)
  - `pocobox-dac-autoplay.service` 구현·배포 (USB DAC 연결 → `plughw:CARD=<ID>,DEV=0` 전환 + 이어재생 / 분리 → 자동 일시정지)
- [x] **Phase 3: 하이브리드 음성 명령(로컬 Fast-Path + Gemini/OpenAI STT + LMS/TIDAL)**
  - `pocobox-voice.service` (`http://pocobox.local:8765`), `pocobox-voice` CLI, 볼륨업 0.7초 길게 누르기 트리거, Phosh 앱 아이콘 구축
  - 84개 한국어/영어 혼합 음성명령 테스트 데이터셋 + 단위/VAD/라이브 TIDAL E2E 자동화 테스트 셋 구축 및 **100% 통과**
- [x] **Phase 4: 무인 상시 운영 안정화** ([03-operations-and-testing.md](docs/03-operations-and-testing.md))
  - 헤드리스 PulseAudio(`pocobox-pulseaudio.service`), Wi-Fi 절전 방지, 자동 슬립 방지, `nftables` 방화벽 규칙 영구 반영, 원클릭 배포 스크립트(`scripts/deploy.sh`)

---

## 빠른 사용

```sh
# SSH 접속
ssh pocobox

# LMS 웹 UI (Material Skin)
open http://pocobox.local:9000/material/

# 음성 명령 API 상태 확인 & 텍스트 테스트
curl -s http://pocobox.local:8765/health
ssh pocobox 'pocobox-voice say "아이유 밤편지 틀어줘"'

# 전체 테스트 셋 실행 (단위 + 84개 데이터셋)
python3 -m unittest discover -s tests -p "test_*.py" -v

# 실제 pocobox 대상 라이브 TIDAL 78개 벤치마크
python3 tests/run_voice_benchmark.py --live http://pocobox.local:8765 --live-limit 100
```

---

## 하드웨어 확장성 (Hardware Portability & Beyond POCO F1)

PocoBox는 기준 기기인 POCO F1 외에도 표준 리눅스(`systemd` + ALSA/PulseAudio + Docker)가 지원되는 다양한 하드웨어 환경에 손쉽게 배포할 수 있습니다.

| 기기 유형 | 대표 기기 예시 | 장점 및 특징 | 오디오 / 디스플레이 구성 |
|---|---|---|---|
| **중고 스마트폰 / 태블릿 (업사이클링)** | POCO F1, OnePlus 6/6T, Pixel 3, LG G7/V35 등 (postmarketOS 메인라인 기기) | **화면 + 배터리(UPS) + 고감도 마이크 + 내장 스피커 + Wi-Fi가 일체형**으로 통합되어 추가 부품 비용 0원으로 상시 구동 가능 | 내장 마이크/스피커로 안내 및 차임 분리 + USB-C DAC 직결 시 Hi-Res Bit-perfect 출력 |
| **싱글보드 컴퓨터 (SBC)** | Raspberry Pi 4 / 5, Orange Pi 5 (RK3588), Radxa Rock | 발열 및 전력 소모가 적고 상시 켜두기 유리하며, 포트 구성이 넉넉함 | USB 마이크 / I2S 마이크 어레이(ReSpeaker 등) + USB DAC + HDMI/DSI 터치스크린 |
| **미니 PC / 구형 노트북 / 씬클라이언트** | Intel NUC, Lenovo ThinkCentre Tiny, 구형 노트북 (x86_64 / arm64) | 강력한 CPU 연산력으로 온디바이스 SLM 및 고속 STT 병렬 처리 최적 | USB DAC 직결, 노트북인 경우 내장 디스플레이·배터리·마이크 즉시 활용 |

### 타 기기 이식 시 확인 사항 (하드웨어 추상화)
1. **USB Host 모드**:
   - Raspberry Pi나 일반 미니 PC는 기본적으로 USB Host 모드로 동작하므로 **DTB 패치 없이 즉시 USB DAC를 인식**합니다.
   - 스마트폰 기종 중 메인라인 커널 기본값이 peripheral인 경우에만 Device Tree `dr_mode = "host"` 설정이 필요합니다.
2. **오디오 경로 분리 (`voice.env` & `dac-autoplay.env`)**:
   - `VOICE_SPEAKER_SINK`: 음성 차임 및 TTS를 출력할 PulseAudio 싱크 이름을 지정합니다 (미지정 시 내장 SoC/온보드 사운드카드 자동 선택, USB DAC 제외).
   - `DAC_OUTPUT_TEMPLATE`: 기본값 `hw:CARD={card_id},DEV=0`으로 모든 리눅스 표준 USB 오디오 장치에 Bit-perfect 직결됩니다.
3. **디스플레이 및 백라이트 (`display.env`)**:
   - 화면이 있는 기기는 `cage + firefox-esr` 풀스크린 키오스크로 Now-Playing 화면을 자동 표시합니다.
   - 백라이트 제어 데몬(`pocobox-backlight`)은 `/sys/class/backlight/*`를 자동 감지하며, 백라이트 인터페이스가 없는 일반 HDMI 모니터나 헤드리스 환경에서는 안전하게 no-op 처리됩니다.
4. **로컬 STT 컨테이너 (`infra/stt/`)**:
   - `sherpa-onnx` 기반 SenseVoice-Small INT8 서버는 ARM64(`aarch64`) 및 x86_64(`amd64`) 모두 멀티 아키텍처를 지원하므로 도커 환경에서 그대로 빌드 및 구동됩니다.

---

## 프로젝트 구조

```text
pocobox/
├── README.md
├── docs/
│   ├── 01-linux-installation.md       # 부트로더 언락 ~ postmarketOS 설치·네트워크 구축 기록
│   ├── 02-project-architecture.md     # LMS(TIDAL) · USB DAC 자동재생 · 하이브리드 음성명령 설계서
│   └── 03-operations-and-testing.md   # 운영 매뉴얼, API 키 설정, 테스트 셋 실행 가이드
├── infra/
│   ├── audio/                         # 헤드리스 PulseAudio systemd 서비스
│   ├── lms/                           # Lyrion Music Server Docker Compose
│   ├── squeezelite/                   # Squeezelite 네이티브 arm64 빌드/서비스
│   ├── system/                        # Wi-Fi 절전 방지, 슬립 방지, nftables 방화벽 규칙
│   ├── usb-dac/                       # USB-C Host DTB 패치/복구 스크립트, DAC 자동재생 서비스
│   └── voice/                         # 하이브리드 음성명령 서비스, env 템플릿, Phosh 앱 아이콘
├── services/
│   ├── lms_client/                    # LMS JSON-RPC + TIDAL 스마트 검색/재생 클라이언트
│   ├── usb_dac_autoplay/              # USB DAC 핫플러그 감시 및 자동 이어재생 데몬
│   └── voice_control/                 # 하이브리드 음성명령 파이프라인 (규칙/별칭/클라우드/VAD/데몬/CLI)
├── tests/
│   ├── dataset/voice_commands_ko_en.json  # 84개 한국어/영어 혼합 음성명령 테스트 데이터셋
│   ├── test_voice_intent_dataset.py       # 데이터셋 정확도·성능 회귀 테스트
│   ├── test_voice_executor_and_cloud.py   # 실행기·TIDAL 폴백·Gemini/OpenAI 모킹·VAD 테스트
│   ├── test_usb_dac_autoplay.py           # USB DAC 핫플러그·이어재생·일시정지 단위 테스트
│   ├── test_live_pocobox_e2e.py           # 실제 pocobox 대상 라이브 E2E 통합 테스트
│   ├── run_voice_benchmark.py             # 카테고리별 정확도 벤치마크 리포트
│   └── generate_audio_samples.sh          # macOS TTS 한국어/영어 WAV 픽스처 생성 및 VAD 검증
└── scripts/
    ├── deploy-phase1.sh
    └── deploy.sh                          # 전체 스택(Phase 1~4) 원클릭 배포 스크립트
```
