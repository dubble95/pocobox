#!/bin/sh
set -eu
rm -f /etc/systemd/system/greetd.service.d/override.conf
systemctl daemon-reload
systemctl disable --now pocobox-backlight.service pocobox-autorotate.service 2>/dev/null || true
systemctl restart greetd.service
echo "==> Restored default Phosh/Phrog greetd display session."
