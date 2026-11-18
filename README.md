# PocoBox

**Xiaomi POCO F1 (`xiaomi-beryllium`)**에 메인라인 리눅스(**postmarketOS / Nura v26.06**)를 설치하고,
**Lyrion Music Server (LMS · TIDAL 중심)**, **USB DAC 연결 시 자동 음악 재생**, **하이브리드 음성 명령 기반 음악 검색·재생** 기능을 갖춘 전용 스마트 뮤직 박스로 구축하는 프로젝트입니다.

---

## 현재 상태

- [x] **Phase 0: 리눅스 설치 및 기본 환경 구축** ([설치 문서](docs/01-linux-installation.md))
  - POCO F1 (`beryllium_global`, `EBBG` 패널) 부트로더 언락 완료
  - postmarketOS (Nura `v26.06` · Phosh · `xiaomi-beryllium-ebbg`) 플래싱 및 부팅 완료
  - 호스트네임 `pocobox` (`pocobox.local` mDNS) 및 Wi-Fi 연결 완료
  - Mac에서 키 기반 비밀번호 없는 SSH 접속 구성 (`ssh pocobox`, `ssh pocobox-usb`)
- [ ] **Phase 1: LMS (Docker) + TIDAL 플러그인 + Squeezelite 로컬 오디오 출력**
- [ ] **Phase 2: USB-C Host/OTG 활성화 + USB DAC 연결 감지 시 자동 전환 & 자동 재생**
- [ ] **Phase 3: 하이브리드 음성 명령(로컬 호출어 + 클라우드 STT/LLM → LMS/TIDAL 검색·재생)**
- [ ] **Phase 4: 안정화, 보안 강화 및 원클릭 배포 자동화**

---

## 빠른 접속 (Mac)

```sh
# Wi-Fi 무선 접속 (mDNS)
ssh pocobox

# USB 유선 접속 (비상/오프라인 시)
ssh pocobox-usb
```

- **LMS 웹 UI (Phase 1 완료 후)**: `http://pocobox.local:9000`

---

## 프로젝트 구조

```text
pocobox/
├── README.md
├── docs/
│   ├── 01-linux-installation.md     # POCO F1 부트로더 언락 ~ postmarketOS 설치·네트워크 구축 기록
│   └── 02-project-architecture.md   # LMS(TIDAL) · USB DAC 자동재생 · 하이브리드 음성명령 설계서
├── infra/
│   ├── lms/                         # Lyrion Music Server Docker Compose 구성
│   ├── squeezelite/                 # Squeezelite 빌드/systemd 유닛
│   └── usb-dac/                     # (Phase 2) USB-C OTG/Host 설정, udev 룰, 자동재생 트리거
├── services/
│   ├── lms_client/                  # LMS JSON-RPC 클라이언트 (TIDAL 검색/재생/제어)
│   ├── usb_dac_autoplay/            # (Phase 2) USB DAC 핫플러그 감지 → Squeezelite 전환 + 자동재생
│   └── voice_control/               # (Phase 3) 하이브리드 음성 명령 서비스
└── scripts/                         # 배포 및 운영 스크립트
```
