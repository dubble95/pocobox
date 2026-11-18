# 03. PocoBox 운영 · 음성명령 · 테스트 매뉴얼

이 문서는 Phase 1~4 구현 완료 후 **PocoBox**의 일상 운영, USB DAC 사용, 하이브리드 음성명령(Gemini / OpenAI) 활성화, 그리고 자동화 테스트 실행 방법을 정리합니다.

---

## 1. 주요 접속 및 서비스 구성

`pocobox` 부팅 시 화면 잠금을 해제하지 않아도 아래 6개 서비스가 자동 기동되며, Wi-Fi 절전 방지(`99-pocobox-no-powersave.conf`) 및 자동 슬립 방지(`99-pocobox-nosuspend.conf`)가 적용되어 24시간 상시 대기합니다.

| 서비스 | 역할 | 포트 / 인터페이스 |
| --- | --- | --- |
| `docker` (`pocobox-lms`) | Lyrion Music Server 9.1.2 (TIDAL, MaterialSkin, YouTube, Favorites) | `http://pocobox.local:9000/material/` |
| `pocobox-pulseaudio.service` | 헤드리스 PulseAudio (내장 마이크 캡처 + 내장 스피커/3.5mm 폴백) | `/run/user/10000/pulse/native` |
| `pocobox-squeezelite.service` | Squeezelite `2.0.0-1612` 플레이어 (`pocobox`, 고정 MAC `02:70:6f:63:6f:01`) | ALSA `plughw:CARD=<DAC>` / `pulse` |
| `pocobox-dac-autoplay.service` | USB DAC 핫플러그 감시 → 출력 자동 전환 → 이어재생 / 분리 시 일시정지 | `udevadm monitor` + `/sys/class/sound` |
| `pocobox-voice.service` | 하이브리드 음성명령 데몬 (HTTP API + 볼륨업 길게 누르기 트리거) | `http://pocobox.local:8765` |
| `avahi-daemon` | mDNS 브로드캐스트 (`pocobox.local`) | UDP 5353 |

방화벽 규칙은 `/etc/nftables.d/55_pocobox.nft`에 등록되어 있어 `3483`, `8765`, `9000`, `9090` 포트가 재부팅 후에도 유지됩니다.

---

## 2. USB DAC 연결 및 자동 재생 사용법

- 현재 커널 DTB가 **`dr_mode = "host"`**로 패치되어 있어 USB-C 포트가 xHCI 2.0/3.0 호스트 모드로 동작합니다 (`lsusb`로 루트 허브 확인).
- **USB DAC 연결 시 자동 동작**:
  1. `pocobox-dac-autoplay`가 USB 재생 오디오 카드(`/sys/class/sound/cardN`, `pcmC*D*p` 보유)를 감지합니다. *(USB 마이크 전용 장치는 제외)*
  2. `/etc/pocobox/squeezelite.env`를 `SQUEEZELITE_OUTPUT=plughw:CARD=<CARD_ID>,DEV=0`으로 전환하고 `pocobox-squeezelite.service`를 재시작합니다.
  3. LMS에서 `pocobox` 플레이어 재연결을 확인한 뒤 **기존 대기열이 있으면 바로 이어서 재생(`play`)**하고, 대기열이 비어 있으면 **TIDAL My Mix**를 자동 재생합니다.
- **USB DAC 분리 시 자동 동작**:
  1. 즉시 LMS에 `pause`를 전송해 내장 스피커로 소리가 갑자기 튀지 않도록 보호합니다.
  2. Squeezelite를 `pulse` 대기 모드로 복귀합니다.
- **전원 참고**: 자체 전원형 데스크톱 DAC는 USB-C OTG 어댑터로 직결 가능하며, 버스 파워 동글 DAC는 POCO F1 메인라인 PMIC의 VBUS 5V 출력 제한 시 **외부 전원 공급형 USB-C PD 허브 / OTG Y-케이블**을 사용하면 충전과 DAC 구동을 동시에 안정적으로 할 수 있습니다.
- **원본 USB Peripheral 모드 복구** (필요 시):
  ```sh
  ssh pocobox 'sudo /srv/pocobox/bin/restore-usb-peripheral-dtb.sh && sudo reboot'
  ```
  *(또한 원본 `boot.img.orig` 백업이 `pocobox:/srv/pocobox/backup/`과 Mac `images/boot.img.orig` 양쪽에 보관되어 있습니다.)*

---

## 3. 하이브리드 음성 명령 활성화 및 사용법

### 3.1 클라우드 STT/Intent API 키 등록 (최초 1회)
텍스트 명령과 로컬 규칙 파서(84개 패턴)는 API 키 없이도 바로 동작합니다. **실제 마이크 음성 인식(STT)**을 켜려면 `pocobox`의 `/etc/pocobox/voice.env`에 **Gemini API 키**(권장: 단일 호출 ~1초) 또는 **OpenAI API 키** 중 하나만 입력하고 서비스를 재시작하세요.

```sh
ssh pocobox 'nano /etc/pocobox/voice.env'
# GEMINI_API_KEY=your_key_here   (권장)
# 또는 OPENAI_API_KEY=your_key_here

ssh pocobox 'sudo systemctl restart pocobox-voice.service'
```

### 3.2 음성 명령 호출 방법 (3가지)
1. **물리 버튼 트리거 (화면 꺼져도 동작)**: POCO F1의 **볼륨 업(+) 버튼을 0.7초 이상 길게 누름** → 시작 알림음(차임) 후 말씀하시면 자동 침묵 감지(VAD)로 녹음 종료·실행!
2. **화면 터치 아이콘**: Phosh 앱 목록의 **"PocoBox 음성명령"** 아이콘 탭
3. **CLI / HTTP API (Mac 또는 `pocobox` 터미널)**:
   ```sh
   # 상태 확인
   curl -s http://pocobox.local:8765/health

   # 텍스트 명령 즉시 실행 (예: TIDAL 검색 재생)
   ssh pocobox 'pocobox-voice say "아이유 밤편지 틀어줘"'
   ssh pocobox 'pocobox-voice say "콜드플레이 픽스 유 틀어줘"'
   ssh pocobox 'pocobox-voice say "마이믹스 틀어줘"'
   ssh pocobox 'pocobox-voice say "볼륨 50으로 해줘"'
   ssh pocobox 'pocobox-voice say "다음 곡"'
   ssh pocobox 'pocobox-voice say "잠깐 멈춰줘"'

   # 마이크 1회 녹음 트리거
   ssh pocobox 'pocobox-voice listen'

   # WAV 음성 파일 테스트
   curl -X POST --data-binary @tests/fixtures/audio/ko_iu_through_the_night.wav \
     -H "Content-Type: audio/wav" "http://pocobox.local:8765/voice/audio?dry_run=1"
   ```

---

## 4. 배포 및 자동화 테스트 셋 실행

### 원클릭 배포 (Mac → `pocobox`)
```sh
./scripts/deploy.sh pocobox
```

### 테스트 셋 구성 및 실행
1. **전체 단위 테스트 + 84개 한국어/영어 혼합 음성명령 데이터셋 회귀 테스트**:
   ```sh
   python3 -m unittest discover -s tests -p "test_*.py" -v
   ```
   - `tests/dataset/voice_commands_ko_en.json`: 10개 카테고리, 84개 실전 한국어/영어/혼합/ASR 오타/호출어/부정(비명령) 케이스
   - `tests/test_voice_intent_dataset.py`: 84개 케이스 정확도 및 지연시간(<5ms/건) 검증
   - `tests/test_voice_executor_and_cloud.py`: TIDAL 다중 쿼리 폴백, 노래방(Karaoke) 트랙 감점, 아티스트 Top Tracks 드릴다운, Gemini/OpenAI 모킹, VAD/무음 트리밍, 차임 WAV 생성 검증
   - `tests/test_usb_dac_autoplay.py`: USB DAC vs USB 마이크 vs 내장 오디오 필터링, 연결 시 이어재생, 분리 시 일시정지 검증
2. **카테고리별 벤치마크 리포트 (오프라인 + 라이브 `pocobox` TIDAL 검색 검증)**:
   ```sh
   # 오프라인 84개 벤치마크
   python3 tests/run_voice_benchmark.py

   # 실제 pocobox LMS/TIDAL 대상 78개 전수 dry-run 검증
   python3 tests/run_voice_benchmark.py --live http://pocobox.local:8765 --live-limit 100
   ```
3. **라이브 E2E 통합 테스트**:
   ```sh
   POCOBOX_LIVE=1 POCOBOX_HOST=pocobox.local python3 -m unittest tests/test_live_pocobox_e2e.py -v
   ```
4. **실제 한국어/영어 TTS 음성 WAV 픽스처 생성 및 VAD 검증 (macOS)**:
   ```sh
   ./tests/generate_audio_samples.sh
   ```
