# PocoBox

**Xiaomi POCO F1 (`xiaomi-beryllium`)**에 메인라인 리눅스(**postmarketOS / Nura v26.06**)를 설치하고,
**Lyrion Music Server (LMS · TIDAL 중심)**, **USB DAC 연결 시 자동 음악 재생**, **하이브리드 음성 명령 기반 음악 검색·재생** 기능을 갖춘 전용 스마트 뮤직 박스로 구축한 프로젝트입니다.

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
