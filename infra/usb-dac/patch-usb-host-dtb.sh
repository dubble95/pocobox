#!/bin/sh
set -eu

DTB="/boot/dtbs/qcom/sdm845-xiaomi-beryllium-ebbg.dtb"
BACKUP_DIR="/srv/pocobox/backup"
TMP_DTS="/tmp/beryllium-usb-host.dts"

mkdir -p "${BACKUP_DIR}"
[ -f "${BACKUP_DIR}/boot.img.orig" ] || cp /boot/boot.img "${BACKUP_DIR}/boot.img.orig"
[ -f "${BACKUP_DIR}/sdm845-xiaomi-beryllium-ebbg.dtb.orig" ] || cp "${DTB}" "${BACKUP_DIR}/sdm845-xiaomi-beryllium-ebbg.dtb.orig"

if ! command -v dtc >/dev/null 2>&1; then
  apk add --no-cache dtc
fi

dtc -I dtb -O dts -o "${TMP_DTS}" "${BACKUP_DIR}/sdm845-xiaomi-beryllium-ebbg.dtb.orig" 2>/dev/null
sed -i 's/dr_mode = "peripheral";/dr_mode = "host";/' "${TMP_DTS}"

if ! grep -q 'dr_mode = "host";' "${TMP_DTS}"; then
  echo "ERROR: failed to patch dr_mode to host" >&2
  exit 1
fi

dtc -I dts -O dtb -o "${DTB}" "${TMP_DTS}" 2>/dev/null
rm -f "${TMP_DTS}"

echo "==> Rebuilding initramfs & boot.img via mkinitfs..."
mkinitfs

echo "==> Done! Verify /boot/boot.img updated and reboot to activate USB Host mode."
ls -lh /boot/boot.img "${DTB}"
