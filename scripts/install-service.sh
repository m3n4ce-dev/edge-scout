#!/usr/bin/env bash
# Run the recorder 24/7 as a user service (survives logout if lingering is enabled: loginctl enable-linger).
set -euo pipefail
mkdir -p ~/.config/systemd/user
cp "$(dirname "$0")/edge-scout.service" ~/.config/systemd/user/edge-scout.service
systemctl --user daemon-reload
systemctl --user enable --now edge-scout
systemctl --user status edge-scout --no-pager | head -5
