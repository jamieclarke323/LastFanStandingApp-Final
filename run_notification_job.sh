#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"
mkdir -p instance

log_path="${LFS_NOTIFICATION_LOG_PATH:-$PWD/instance/notifications.log}"
exec "${PYTHON_BIN:-python3}" run_notification_job.py >> "$log_path" 2>&1
