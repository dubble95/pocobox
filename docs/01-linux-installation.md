# 01. POCO F1 리눅스(postmarketOS) 설치 및 초기 설정 기록

이 문서는 macOS(Intel x86_64) 환경에서 **Xiaomi POCO F1 (`xiaomi-beryllium`)**의 부트로더를 공식 절차로 언락하고, **postmarketOS (Nura v26.06 · Phosh)**를 설치한 뒤 Wi-Fi 및 SSH(`pocobox.local`)까지 구축한 전체 과정을 정리한 기록입니다.

---

## 1. 초기 기기 진단 결과

Homebrew로 `android-platform-tools`(`adb`, `fastboot`)를 설치한 뒤 USB 디버깅을 통해 기기 정보를 먼저 확인했습니다.

```sh
brew install --cask android-platform-tools
adb devices -l
```

| 항목 | 확인된 값 | 비고 |
| --- | --- | --- |
| **모델 / 코드네임** | `POCOPHONE F1` (`beryllium_global`) | 글로벌판 |
| **기존 OS / 펌웨어** | MIUI 11 (`V11.0.5.0.PEJMIXM`, Android 9) | 추후 필요 시 MIUI 12(Android 10) 펌웨어 업데이트 가능 |
| **디스플레이/터치 패널** | **`EBBG`** (`fts_ts`, FocalTech FT8719) | `adb shell dumpsys input`에서 `Device 6: fts_ts`로 확인 |
| **부트로더 초기 상태** | 잠김 (`ro.boot.flash.locked = 1`) | 언락 필요 |

> **패널 판별 참고**: POCO F1은 `Tianma`(Novatek NT36672A)와 `EBBG`(FocalTech FT8719) 두 가지 패널 변형이 있으며, 잘못된 이미지를 플래싱하면 화면/터치가 동작하지 않습니다. `dumpsys input`에서 `fts_ts`가 확인되어 `xiaomi-beryllium-ebbg` 이미지를 선택했습니다.

---

## 2. 부트로더 공식 언락 (macOS + MiUnlockTool)

Windows 전용 공식 MiFlashUnlock 대신, 샤오미 공식 언락 API와 통신하는 오픈소스 CLI 도구(`offici5l/MiUnlockTool`)를 Python 가상환경(`.venv`)에 설치하여 진행했습니다.

### 2.1 도구 준비
```sh
git clone --depth 1 https://github.com/offici5l/MiUnlockTool.git
python3 -m venv .venv
.venv/bin/pip install ./MiUnlockTool/MiUnlockTool
```

### 2.2 진행 중 해결한 오류 기록
1. **브라우저 로그인 유휴 연결 끊김 (`RemoteDisconnected`)**
   - 로그인 방식 선택 프롬프트에서 입력이 지연되면 샤오미 `account.xiaomi.com` keep-alive 소켓이 먼저 닫히는 현상이 있었으며, 선택(`1 - Browser`)을 즉시 전달해 브라우저 로그인을 정상 완료했습니다.
2. **오류 코드 `20041` (`Sorry, your MI ID is not associated with a phone number.`)**
   - 샤오미 계정 페이지(`https://account.xiaomi.com/pass/serviceLogin?checkSafePhone=true`)에서 전화번호를 등록·인증하여 해결했습니다.
3. **오류 코드 `20031` (`Please add your account in Settings > Developer options > Mi Unlock status.`)**
   - 공기계 상태였으나, 바인딩 시점에만 유심(SIM)을 잠시 꽂고 **Wi-Fi를 끈 모바일 데이터 상태**에서 `설정 → 추가 설정 → 개발자 옵션 → Mi 잠금 해제 상태 → 계정 및 기기 추가`를 완료하여 해결했습니다.

### 2.3 Fastboot 언락 실행
```sh
adb reboot bootloader
fastboot devices -l
PATH="/usr/local/bin:$PATH" .venv/bin/miunlock
```
- 대기 시간 없이 즉시 `Unlock successful`이 반환되었고, 기기가 자동 초기화 후 재부팅되었습니다.
- 다시 Fastboot(`전원 + 볼륨 아래`) 진입 후 `fastboot getvar unlocked` → `unlocked: yes` 확인 완료.

---

## 3. postmarketOS (Nura v26.06 · Phosh) 다운로드 및 플래싱

공식 이미지 저장소(`https://images.nura.eco/bpo/v26.06/xiaomi-beryllium/phosh/20261113-1430/`)에서 `ebbg` 패널용 최신 프리빌드 이미지를 받아 무결성 검증 후 플래싱했습니다.

### 3.1 다운로드 · SHA-256 검증 · 압축 해제
```sh
mkdir -p images && cd images
BASE="https://images.nura.eco/bpo/v26.06/xiaomi-beryllium/phosh/20261113-1430"
BOOT="20261113-1430-nura-v26.06-phosh-xiaomi-beryllium-ebbg-boot.img.xz"
ROOT="20261113-1430-nura-v26.06-phosh-xiaomi-beryllium-ebbg.img.xz"

curl -fL -O "${BASE}/${BOOT}" -O "${BASE}/${BOOT}.sha256" \
         -O "${BASE}/${ROOT}" -O "${BASE}/${ROOT}.sha256"
shasum -a 256 -c "${BOOT}.sha256" "${ROOT}.sha256"
unxz -k -f -v "${BOOT}" "${ROOT}"
```

### 3.2 Fastboot 플래싱
```sh
fastboot flash boot 20261113-1430-nura-v26.06-phosh-xiaomi-beryllium-ebbg-boot.img
fastboot flash userdata 20261113-1430-nura-v26.06-phosh-xiaomi-beryllium-ebbg.img
fastboot reboot
```

> **주의**: `userdata` 플래싱 직후 전원 버튼으로 강제 재부팅하면 UFS 쓰기 동기화가 끝나지 않아 파일시스템이 손상될 수 있습니다. 반드시 `fastboot reboot` 명령을 사용해야 합니다 (실제 재부팅 동기화에 약 32초 소요).

---

## 4. SSH · 호스트네임(`pocobox`) · Wi-Fi 초기 설정

### 4.1 USB 네트워크 부팅 확인 및 `sshd` 활성화
- 부팅 후 Mac에서 USB 네트워크 인터페이스(`172.16.42.2/24`)가 자동 생성되었고, 기기(`172.16.42.1`) ping 정상 확인.
- 프리빌드 GUI 이미지는 `sshd`가 기본 비활성화되어 있으므로, POCO F1의 Console 앱에서 활성화:
  ```sh
  sudo systemctl enable --now sshd
  ```
- 기본 계정: `user` / 초기 PIN·비밀번호: `147147` *(추후 변경 권장)*

### 4.2 Mac SSH 키 등록 및 단축 접속 설정
```sh
ssh-copy-id -i ~/.ssh/id_ed25519.pub user@172.16.42.1
```

### 4.3 호스트네임 `pocobox` 변경 및 mDNS(`pocobox.local`)
```sh
sudo hostnamectl set-hostname pocobox
sudo sed -i 's/xiaomi-beryllium/pocobox/g' /etc/hosts
sudo systemctl restart avahi-daemon
```

### 4.4 Wi-Fi 프로파일 등록 (`nmcli`)
사용 중인 2.4GHz 및 5GHz Wi-Fi 대역을 등록하고 인터넷 통신(`1.1.1.1`)을 확인합니다.
```sh
sudo nmcli device wifi connect "<WIFI_SSID_5G>" password "<WIFI_PASSWORD>"
sudo nmcli device wifi connect "<WIFI_SSID_2G>" password "<WIFI_PASSWORD>"
```

### 4.5 Mac `~/.ssh/config`
```sshconfig
Host pocobox
  HostName pocobox.local
  User user
  IdentityFile ~/.ssh/id_ed25519
Host pocobox-usb
  HostName 172.16.42.1
  User user
  IdentityFile ~/.ssh/id_ed25519
```

---

## 5. 설치 완료 후 하드웨어 및 커널 점검 스냅샷

`ssh pocobox`로 확인한 실제 시스템 사양 및 커널 기능:

- **OS**: `postmarketOS v26.06` (Alpine 기반, `systemd` init, `apk-tools 3.0.9`)
- **커널**: `Linux pocobox 7.1.0-rc1-sdm845 #1-postmarketos-qcom-sdm845 SMP PREEMPT aarch64`
- **CPU / RAM / 저장공간**: 8코어 (SDM845) / RAM 5.4 GiB (가용 ~3.1 GiB) / 루트 파티션 51.3 GiB (여유 46.3 GiB)
- **오디오**:
  - **PulseAudio 17.0** 활성 (`Default Sink`: `HiFi__Speaker1__sink`, `Default Source`: `HiFi__Mic1__source`)
  - ALSA 카드 0 (`Xiaomi Poco F1`) 재생/캡처 디바이스 정상 인식
- **컨테이너 지원**: `apk`에서 `docker`, `docker-cli-compose`, `docker-systemd` 제공 및 커널 옵션(`OVERLAY_FS`, `VETH`, `BRIDGE`, `NETFILTER`) 포함 확인
- **USB / OTG 상태 (중요 확인 사항)**:
  - 커널 모듈 `CONFIG_USB_STORAGE=m`, `CONFIG_SND_USB_AUDIO=m`, `CONFIG_USB_DWC3=y`는 포함되어 있음
  - 단, 현재 기본 Device Tree에서 USB-C 컨트롤러 모드가 **`dr_mode = peripheral`**(`/proc/device-tree/soc@0/usb@a6f8800/usb@a600000/dr_mode`)로 설정되어 있어, 외부 USB 장치를 직접 연결하려면 **Phase 2에서 USB Host/OTG 전환 검증**(또는 microSD 병행 지원)이 필요함
