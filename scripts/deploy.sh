#!/usr/bin/env bash
set -euo pipefail

HOST="${1:-pocobox}"
REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"

echo "==> Deploying PocoBox full stack (Phases 1-4) to ${HOST}..."

ssh "${HOST}" 'mkdir -p /tmp/pocobox-deploy/voice_control /tmp/pocobox-deploy/stt'

scp "${REPO_DIR}/infra/lms/docker-compose.yml" "${HOST}:/tmp/pocobox-deploy/"
scp "${REPO_DIR}"/infra/stt/* "${HOST}:/tmp/pocobox-deploy/stt/"
scp "${REPO_DIR}/infra/audio/pocobox-pulseaudio.service" "${HOST}:/tmp/pocobox-deploy/"
scp "${REPO_DIR}/infra/audio/pocobox_aec3.cpp" "${HOST}:/tmp/pocobox-deploy/"
scp "${REPO_DIR}/infra/audio/build-aec3.sh" "${HOST}:/tmp/pocobox-deploy/"
scp "${REPO_DIR}/infra/squeezelite/squeezelite.env" "${HOST}:/tmp/pocobox-deploy/"
scp "${REPO_DIR}/infra/squeezelite/pocobox-squeezelite.service" "${HOST}:/tmp/pocobox-deploy/"
scp "${REPO_DIR}/infra/squeezelite/build-squeezelite.sh" "${HOST}:/tmp/pocobox-deploy/"
scp "${REPO_DIR}/infra/usb-dac/dac-autoplay.env" "${HOST}:/tmp/pocobox-deploy/"
scp "${REPO_DIR}/infra/usb-dac/pocobox-dac-autoplay.service" "${HOST}:/tmp/pocobox-deploy/"
scp "${REPO_DIR}/infra/usb-dac/patch-usb-host-dtb.sh" "${HOST}:/tmp/pocobox-deploy/"
scp "${REPO_DIR}/infra/usb-dac/restore-usb-peripheral-dtb.sh" "${HOST}:/tmp/pocobox-deploy/"
scp "${REPO_DIR}/infra/voice/voice.env.example" "${HOST}:/tmp/pocobox-deploy/"
scp "${REPO_DIR}/infra/voice/pocobox-voice.service" "${HOST}:/tmp/pocobox-deploy/"
scp "${REPO_DIR}/infra/voice/pocobox-llama.service" "${HOST}:/tmp/pocobox-deploy/"
scp "${REPO_DIR}/infra/voice/pocobox-daily-quiz.service" "${HOST}:/tmp/pocobox-deploy/"
scp "${REPO_DIR}/infra/voice/pocobox-daily-quiz.timer" "${HOST}:/tmp/pocobox-deploy/"
scp "${REPO_DIR}/infra/voice/pocobox-voice.desktop" "${HOST}:/tmp/pocobox-deploy/"
scp "${REPO_DIR}/infra/system/99-pocobox-no-powersave.conf" "${HOST}:/tmp/pocobox-deploy/"
scp "${REPO_DIR}/infra/system/99-pocobox-nosuspend.conf" "${HOST}:/tmp/pocobox-deploy/"
scp "${REPO_DIR}/infra/system/55_pocobox.nft" "${HOST}:/tmp/pocobox-deploy/"
scp "${REPO_DIR}/infra/display/greetd-kiosk.toml" "${HOST}:/tmp/pocobox-deploy/"
scp "${REPO_DIR}/infra/display/display.env" "${HOST}:/tmp/pocobox-deploy/"
scp "${REPO_DIR}/infra/display/pocobox-kiosk-start.sh" "${HOST}:/tmp/pocobox-deploy/"
scp "${REPO_DIR}/infra/display/pocobox-autorotate.py" "${HOST}:/tmp/pocobox-deploy/"
scp "${REPO_DIR}/infra/display/pocobox-autorotate.service" "${HOST}:/tmp/pocobox-deploy/"
scp "${REPO_DIR}/infra/display/pocobox-backlight.py" "${HOST}:/tmp/pocobox-deploy/"
scp "${REPO_DIR}/infra/display/pocobox-backlight.service" "${HOST}:/tmp/pocobox-deploy/"
scp "${REPO_DIR}/infra/display/99-pocobox-kiosk.conf" "${HOST}:/tmp/pocobox-deploy/"
scp "${REPO_DIR}/infra/display/95-pocobox-touchscreen.rules" "${HOST}:/tmp/pocobox-deploy/"
scp "${REPO_DIR}/infra/display/restore-phosh-display.sh" "${HOST}:/tmp/pocobox-deploy/"
scp "${REPO_DIR}/services/lms_client/lms_client.py" "${HOST}:/tmp/pocobox-deploy/"
scp "${REPO_DIR}/services/usb_dac_autoplay/pocobox_dac_autoplay.py" "${HOST}:/tmp/pocobox-deploy/"
scp "${REPO_DIR}/tests/run_wake_regression.py" "${HOST}:/tmp/pocobox-deploy/"
scp "${REPO_DIR}"/services/voice_control/*.py "${HOST}:/tmp/pocobox-deploy/voice_control/"

SSH_TTY_OPT=""
[ -t 0 ] && SSH_TTY_OPT="-tt"
ssh ${SSH_TTY_OPT} "${HOST}" 'sudo sh -c "
set -eu
SRC=/tmp/pocobox-deploy
mkdir -p /srv/pocobox/lms/config /srv/pocobox/lms/music /srv/pocobox/lms/playlist /srv/pocobox/stt /srv/pocobox/bin/voice_control /srv/pocobox/logs/wake_clips /srv/pocobox/logs/command_clips /etc/pocobox /etc/NetworkManager/conf.d /etc/systemd/sleep.conf.d /etc/systemd/system/greetd.service.d /home/user/.local/share/applications

install -m 0644 \$SRC/docker-compose.yml /srv/pocobox/lms/docker-compose.yml
install -m 0644 \$SRC/stt/Dockerfile /srv/pocobox/stt/Dockerfile
install -m 0644 \$SRC/stt/docker-compose.yml /srv/pocobox/stt/docker-compose.yml
install -m 0755 \$SRC/stt/stt_server.py /srv/pocobox/stt/stt_server.py
install -m 0755 \$SRC/build-squeezelite.sh /srv/pocobox/bin/build-squeezelite.sh
install -m 0644 \$SRC/pocobox_aec3.cpp /srv/pocobox/bin/pocobox_aec3.cpp
install -m 0755 \$SRC/build-aec3.sh /srv/pocobox/bin/build-aec3.sh
install -m 0755 \$SRC/patch-usb-host-dtb.sh /srv/pocobox/bin/patch-usb-host-dtb.sh
install -m 0755 \$SRC/restore-usb-peripheral-dtb.sh /srv/pocobox/bin/restore-usb-peripheral-dtb.sh
install -m 0755 \$SRC/pocobox-kiosk-start.sh /srv/pocobox/bin/pocobox-kiosk-start.sh
install -m 0755 \$SRC/pocobox-autorotate.py /srv/pocobox/bin/pocobox-autorotate.py
install -m 0755 \$SRC/pocobox-backlight.py /srv/pocobox/bin/pocobox-backlight.py
install -m 0755 \$SRC/restore-phosh-display.sh /srv/pocobox/bin/restore-phosh-display.sh
install -m 0755 \$SRC/lms_client.py /srv/pocobox/bin/lms_client.py
install -m 0755 \$SRC/pocobox_dac_autoplay.py /srv/pocobox/bin/pocobox_dac_autoplay.py
install -m 0755 \$SRC/run_wake_regression.py /srv/pocobox/bin/run_wake_regression.py
install -m 0644 \$SRC/voice_control/*.py /srv/pocobox/bin/voice_control/
chmod +x /srv/pocobox/bin/voice_control/daemon.py /srv/pocobox/bin/voice_control/cli.py

ln -sf /srv/pocobox/bin/lms_client.py /usr/local/bin/pocobox-lms
ln -sf /srv/pocobox/bin/voice_control/cli.py /usr/local/bin/pocobox-voice

[ -f /etc/pocobox/squeezelite.env ] || install -m 0644 \$SRC/squeezelite.env /etc/pocobox/squeezelite.env
[ -f /etc/pocobox/dac-autoplay.env ] || install -m 0644 \$SRC/dac-autoplay.env /etc/pocobox/dac-autoplay.env
[ -f /etc/pocobox/display.env ] || install -m 0644 \$SRC/display.env /etc/pocobox/display.env
grep -q "DISPLAY_ROTATION" /etc/pocobox/display.env 2>/dev/null || cat << "DISP_EOF" >> /etc/pocobox/display.env

# Screen rotation: auto (accelerometer via iio-sensor-proxy), or fixed normal|90|180|270
DISPLAY_ROTATION=auto
DISPLAY_POWER_MODE=auto
DISPLAY_IDLE_OFF_SEC=10
DISPLAY_TOUCH_WAKE_SEC=15
DISP_EOF
[ -f /etc/pocobox/voice.env ] || install -m 0600 -o user -g user \$SRC/voice.env.example /etc/pocobox/voice.env
grep -q "ENABLE_LOCAL_LLM" /etc/pocobox/voice.env 2>/dev/null || cat << "ENV_EOF" >> /etc/pocobox/voice.env

# Tier 3: Local SLM (Qwen2.5-0.5B via llama-server on Kryo CPU)
ENABLE_LOCAL_LLM=1
LOCAL_LLM_URL=http://127.0.0.1:8767
LOCAL_LLM_TIMEOUT=3.5
ENV_EOF
install -m 0644 \$SRC/greetd-kiosk.toml /etc/pocobox/greetd-kiosk.toml

install -m 0644 \$SRC/pocobox-pulseaudio.service /etc/systemd/system/pocobox-pulseaudio.service
install -m 0644 \$SRC/pocobox-squeezelite.service /etc/systemd/system/pocobox-squeezelite.service
install -m 0644 \$SRC/pocobox-dac-autoplay.service /etc/systemd/system/pocobox-dac-autoplay.service
install -m 0644 \$SRC/pocobox-llama.service /etc/systemd/system/pocobox-llama.service
install -m 0644 \$SRC/pocobox-voice.service /etc/systemd/system/pocobox-voice.service
install -m 0644 \$SRC/pocobox-daily-quiz.service /etc/systemd/system/pocobox-daily-quiz.service
install -m 0644 \$SRC/pocobox-daily-quiz.timer /etc/systemd/system/pocobox-daily-quiz.timer
install -m 0644 \$SRC/pocobox-backlight.service /etc/systemd/system/pocobox-backlight.service
install -m 0644 \$SRC/pocobox-autorotate.service /etc/systemd/system/pocobox-autorotate.service
install -m 0644 \$SRC/99-pocobox-kiosk.conf /etc/systemd/system/greetd.service.d/override.conf
mkdir -p /etc/udev/rules.d
install -m 0644 \$SRC/95-pocobox-touchscreen.rules /etc/udev/rules.d/95-pocobox-touchscreen.rules
udevadm control --reload-rules
udevadm trigger --subsystem-match=input --action=change 2>/dev/null || true

install -m 0644 \$SRC/99-pocobox-no-powersave.conf /etc/NetworkManager/conf.d/99-pocobox-no-powersave.conf
install -m 0644 \$SRC/99-pocobox-nosuspend.conf /etc/systemd/sleep.conf.d/99-pocobox-nosuspend.conf
install -m 0644 \$SRC/55_pocobox.nft /etc/nftables.d/55_pocobox.nft
install -m 0644 -o user -g user \$SRC/pocobox-voice.desktop /home/user/.local/share/applications/pocobox-voice.desktop

addgroup user input 2>/dev/null || true
addgroup user video 2>/dev/null || true
chown -R user:user /srv/pocobox/bin /srv/pocobox/logs /srv/pocobox/stt /home/user/.local/share/applications

if [ -d /srv/pocobox/models/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-int8-2024-07-17 ] && command -v docker >/dev/null 2>&1; then
  (cd /srv/pocobox/stt && docker compose up -d --build) || echo WARN_pocobox_stt_start_skipped
fi

if [ ! -f /usr/local/lib/libpocobox_aec3.so ] || [ /srv/pocobox/bin/pocobox_aec3.cpp -nt /usr/local/lib/libpocobox_aec3.so ]; then
  /srv/pocobox/bin/build-aec3.sh || echo WARN_libpocobox_aec3_build_skipped
fi
if [ -x /usr/local/bin/squeezelite ] && ! /usr/local/bin/squeezelite -? 2>&1 | grep -qi visexport; then
  /srv/pocobox/bin/build-squeezelite.sh || echo WARN_squeezelite_VISEXPORT_rebuild_skipped
fi

if [ -f /srv/pocobox/models/qwen2.5-0.5b-instruct-q4_k_m.gguf ] && command -v llama-server >/dev/null 2>&1; then
  systemctl daemon-reload
  systemctl enable --now pocobox-llama.service || echo WARN_pocobox_llama_start_skipped
fi

rm -rf \$SRC

if ! command -v wlr-randr >/dev/null 2>&1 && command -v apk >/dev/null 2>&1; then
  apk add --no-cache wlr-randr || true
fi

systemctl daemon-reload
systemctl restart nftables 2>/dev/null || nft -f /etc/nftables.d/55_pocobox.nft 2>/dev/null || true
systemctl reload NetworkManager 2>/dev/null || true
systemctl enable --now pocobox-pulseaudio.service pocobox-squeezelite.service pocobox-dac-autoplay.service pocobox-voice.service pocobox-daily-quiz.timer pocobox-backlight.service pocobox-autorotate.service
systemctl restart pocobox-dac-autoplay.service pocobox-voice.service greetd.service pocobox-backlight.service pocobox-autorotate.service
"'
echo "==> Deploy complete!"
