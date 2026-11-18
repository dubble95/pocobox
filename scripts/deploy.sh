#!/usr/bin/env bash
set -euo pipefail

HOST="${1:-pocobox}"
REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"

echo "==> Deploying PocoBox full stack (Phases 1-4) to ${HOST}..."

ssh "${HOST}" 'mkdir -p /tmp/pocobox-deploy/voice_control'

scp "${REPO_DIR}/infra/lms/docker-compose.yml" "${HOST}:/tmp/pocobox-deploy/"
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
scp "${REPO_DIR}/infra/voice/pocobox-voice.desktop" "${HOST}:/tmp/pocobox-deploy/"
scp "${REPO_DIR}/infra/system/99-pocobox-no-powersave.conf" "${HOST}:/tmp/pocobox-deploy/"
scp "${REPO_DIR}/infra/system/99-pocobox-nosuspend.conf" "${HOST}:/tmp/pocobox-deploy/"
scp "${REPO_DIR}/infra/system/55_pocobox.nft" "${HOST}:/tmp/pocobox-deploy/"
scp "${REPO_DIR}/infra/display/greetd-kiosk.toml" "${HOST}:/tmp/pocobox-deploy/"
scp "${REPO_DIR}/infra/display/display.env" "${HOST}:/tmp/pocobox-deploy/"
scp "${REPO_DIR}/infra/display/pocobox-kiosk-start.sh" "${HOST}:/tmp/pocobox-deploy/"
scp "${REPO_DIR}/infra/display/pocobox-backlight.service" "${HOST}:/tmp/pocobox-deploy/"
scp "${REPO_DIR}/infra/display/99-pocobox-kiosk.conf" "${HOST}:/tmp/pocobox-deploy/"
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
mkdir -p /srv/pocobox/lms/config /srv/pocobox/lms/music /srv/pocobox/lms/playlist /srv/pocobox/bin/voice_control /srv/pocobox/logs/wake_clips /etc/pocobox /etc/NetworkManager/conf.d /etc/systemd/sleep.conf.d /etc/systemd/system/greetd.service.d /home/user/.local/share/applications

install -m 0644 \$SRC/docker-compose.yml /srv/pocobox/lms/docker-compose.yml
install -m 0755 \$SRC/build-squeezelite.sh /srv/pocobox/bin/build-squeezelite.sh
install -m 0644 \$SRC/pocobox_aec3.cpp /srv/pocobox/bin/pocobox_aec3.cpp
install -m 0755 \$SRC/build-aec3.sh /srv/pocobox/bin/build-aec3.sh
install -m 0755 \$SRC/patch-usb-host-dtb.sh /srv/pocobox/bin/patch-usb-host-dtb.sh
install -m 0755 \$SRC/restore-usb-peripheral-dtb.sh /srv/pocobox/bin/restore-usb-peripheral-dtb.sh
install -m 0755 \$SRC/pocobox-kiosk-start.sh /srv/pocobox/bin/pocobox-kiosk-start.sh
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
[ -f /etc/pocobox/voice.env ] || install -m 0600 -o user -g user \$SRC/voice.env.example /etc/pocobox/voice.env
install -m 0644 \$SRC/greetd-kiosk.toml /etc/pocobox/greetd-kiosk.toml

install -m 0644 \$SRC/pocobox-pulseaudio.service /etc/systemd/system/pocobox-pulseaudio.service
install -m 0644 \$SRC/pocobox-squeezelite.service /etc/systemd/system/pocobox-squeezelite.service
install -m 0644 \$SRC/pocobox-dac-autoplay.service /etc/systemd/system/pocobox-dac-autoplay.service
install -m 0644 \$SRC/pocobox-voice.service /etc/systemd/system/pocobox-voice.service
install -m 0644 \$SRC/pocobox-backlight.service /etc/systemd/system/pocobox-backlight.service
install -m 0644 \$SRC/99-pocobox-kiosk.conf /etc/systemd/system/greetd.service.d/override.conf

install -m 0644 \$SRC/99-pocobox-no-powersave.conf /etc/NetworkManager/conf.d/99-pocobox-no-powersave.conf
install -m 0644 \$SRC/99-pocobox-nosuspend.conf /etc/systemd/sleep.conf.d/99-pocobox-nosuspend.conf
install -m 0644 \$SRC/55_pocobox.nft /etc/nftables.d/55_pocobox.nft
install -m 0644 -o user -g user \$SRC/pocobox-voice.desktop /home/user/.local/share/applications/pocobox-voice.desktop

addgroup user input 2>/dev/null || true
addgroup user video 2>/dev/null || true
chown -R user:user /srv/pocobox/bin /srv/pocobox/logs /home/user/.local/share/applications

if [ ! -f /usr/local/lib/libpocobox_aec3.so ] || [ /srv/pocobox/bin/pocobox_aec3.cpp -nt /usr/local/lib/libpocobox_aec3.so ]; then
  /srv/pocobox/bin/build-aec3.sh || echo WARN_libpocobox_aec3_build_skipped
fi
if [ -x /usr/local/bin/squeezelite ] && ! /usr/local/bin/squeezelite -? 2>&1 | grep -qi visexport; then
  /srv/pocobox/bin/build-squeezelite.sh || echo WARN_squeezelite_VISEXPORT_rebuild_skipped
fi

rm -rf \$SRC

systemctl daemon-reload
systemctl restart nftables 2>/dev/null || nft -f /etc/nftables.d/55_pocobox.nft 2>/dev/null || true
systemctl reload NetworkManager 2>/dev/null || true
systemctl enable --now pocobox-pulseaudio.service pocobox-squeezelite.service pocobox-dac-autoplay.service pocobox-voice.service pocobox-backlight.service
systemctl restart pocobox-dac-autoplay.service pocobox-voice.service greetd.service
"'
echo "==> Deploy complete!"
