#!/bin/sh
set -eu

DTB="/boot/dtbs/qcom/sdm845-xiaomi-beryllium-ebbg.dtb"
BACKUP_DTB="/srv/pocobox/backup/sdm845-xiaomi-beryllium-ebbg.dtb.orig"

if [ ! -f "${BACKUP_DTB}" ]; then
  echo "ERROR: backup DTB not found at ${BACKUP_DTB}" >&2
  exit 1
fi

cp "${BACKUP_DTB}" "${DTB}"
mkinitfs
echo "==> Restored original peripheral DTB. Reboot to apply."
