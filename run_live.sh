#!/usr/bin/env zsh
# Starts the LIVE version of Last Fan Standing.
# Notifications feature is hidden. Uses the production database.
# Port: 5000
cd "$(dirname "$0")"
LFS_PORT=5000 \
LFS_DB_NAME=last_fan_standing.db \
LFS_NOTIFICATIONS=0 \
.venv/bin/python app.py
