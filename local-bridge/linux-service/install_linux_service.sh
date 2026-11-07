#!/usr/bin/env bash
set -euo pipefail

INSTALL_DIR="/opt/powerpos/local-bridge"
CONFIG_DIR="/etc/powerpos"
STATE_DIR="/var/lib/powerpos"

id -u powerpos &>/dev/null || useradd --system --no-create-home --groups dialout,lp powerpos
mkdir -p "$INSTALL_DIR" "$CONFIG_DIR" "$STATE_DIR"
cp -r ../python ../windows-service "$INSTALL_DIR/"
cp powerpos-local-bridge.service /etc/systemd/system/powerpos-local-bridge.service
chown -R powerpos:dialout "$INSTALL_DIR" "$STATE_DIR"
systemctl daemon-reload
systemctl enable powerpos-local-bridge.service
echo "PowerPOS Local Bridge systemd unit installed."

