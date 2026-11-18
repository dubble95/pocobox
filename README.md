# PocoBox

**Xiaomi POCO F1 (`xiaomi-beryllium`)**에 메인라인 리눅스(**postmarketOS / Nura v26.06**)를 설치하고,
**Lyrion Music Server (LMS)**, **외부 USB 연결 시 자동 음악 재생**, **음성 명령 기반 음악 검색 및 재생** 기능을 갖춘 전용 스마트 뮤직 박스로 구축하는 프로젝트입니다.

---

## 현재 상태

- [x] **Phase 0: 리눅스 설치 및 기본 환경 구축** ([설치 문서](docs/01-linux-installation.md))
  - POCO F1 (`beryllium_global`, `EBBG` 패널) 부트로더 언락 완료
  - postmarketOS (Nura `v26.06` · Phosh · `xiaomi-beryllium-ebbg`) 플래싱 및 부팅 완료
  - 호스트네임 `pocobox` (`pocobox.local` mDNS) 및 Wi-Fi 연결 완료
  - Mac에서 키 기반 비밀번호 없는 SSH 접속 구성 (`ssh pocobox`, `ssh pocobox-usb`)
- [ ] **Phase 1: LMS (Lyrion Music Server) + Squeezelite 오디오 출력 구성**
- [ ] **Phase 2: 외부 USB (및 microSD) 연결 감지 → 자동 마운트 & 자동 음악 재생**
- [ ] **Phase 3: 음성 명령(한국어/영어) 음악 검색 및 제어 파이프라인**
- [ ] **Phase 4: 안정화, 대시보드/상태 알림 및 자동 복구**

---

## 빠른 접속 (Mac)

```sh
# Wi-Fi 무선 접속 (mDNS)
ssh pocobox

# USB 유선 접속 (비상/오프라인 시)
ssh pocobox-usb
```

---

## 프로젝트 구조

```text
pocobox/
├── README.md
├── docs/
│   ├── 01-linux-installation.md     # POCO F1 부트로더 언락 ~ postmarketOS 설치·네트워크 구축 기록
│   └── 02-project-architecture.md   # LMS · USB 자동재생 · 음성명령 프로젝트 설계 및 마일스톤
├── infra/                           # (예정) Docker Compose(LMS), systemd 유닛, udev 룰, 설치 스크립트
└── services/                        # (예정) USB 자동재생 데몬, 음성 명령(STT/Intent/LMS) 서비스
```
