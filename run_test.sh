#!/usr/bin/env zsh
# Starts the TEST version of Last Fan Standing.
# Notifications feature is ON. Uses a separate test database so live data is untouched.
# Port: 5001  ->  http://127.0.0.1:5001  (or your LAN IP:5001 from a phone)
cd "$(dirname "$0")"
LFS_PORT=5001 \
LFS_DB_NAME=last_fan_standing_test.db \
LFS_NOTIFICATIONS=1 \
.venv/bin/python app.py
