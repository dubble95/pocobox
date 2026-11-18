#!/usr/bin/env bash
set -euo pipefail

HOST="${1:-pocobox}"
REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"

echo "==> Syncing Phase 1 files to ${HOST}:/srv/pocobox ..."
ssh -tt "${HOST}" 'sudo mkdir -p /srv/pocobox/lms/{config,music,playlist} /srv/pocobox/bin /etc/pocobox && sudo chown -R user:user /srv/pocobox'

scp "${REPO_DIR}/infra/lms/docker-compose.yml" "${HOST}:/srv/pocobox/lms/docker-compose.yml"
scp "${REPO_DIR}/infra/squeezelite/build-squeezelite.sh" "${HOST}:/srv/pocobox/bin/build-squeezelite.sh"
scp "${REPO_DIR}/infra/squeezelite/squeezelite.env" "${HOST}:/tmp/squeezelite.env"
scp "${REPO_DIR}/infra/squeezelite/pocobox-squeezelite.service" "${HOST}:/tmp/pocobox-squeezelite.service"
scp "${REPO_DIR}/services/lms_client/lms_client.py" "${HOST}:/srv/pocobox/bin/lms_client.py"

ssh -tt "${HOST}" '
set -eu
chmod +x /srv/pocobox/bin/build-squeezelite.sh /srv/pocobox/bin/lms_client.py
sudo ln -sf /srv/pocobox/bin/lms_client.py /usr/local/bin/pocobox-lms

if [ ! -f /etc/pocobox/squeezelite.env ]; then
  sudo install -m 0644 /tmp/squeezelite.env /etc/pocobox/squeezelite.env
fi
sudo install -m 0644 /tmp/pocobox-squeezelite.service /etc/systemd/system/pocobox-squeezelite.service
rm -f /tmp/squeezelite.env /tmp/pocobox-squeezelite.service

if [ ! -x /usr/local/bin/squeezelite ]; then
  echo "==> Building squeezelite natively..."
  /srv/pocobox/bin/build-squeezelite.sh
fi

echo "==> Starting Lyrion Music Server (Docker)..."
cd /srv/pocobox/lms
sudo docker compose up -d

echo "==> Starting pocobox-squeezelite.service..."
sudo systemctl daemon-reload
sudo systemctl enable --now pocobox-squeezelite.service

echo "==> Verifying services..."
sudo docker ps --filter name=pocobox-lms
sudo systemctl --no-pager -l status pocobox-squeezelite.service | head -n 15
'
