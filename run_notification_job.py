"""Hourly scheduled task entry point (PythonAnywhere "Scheduled tasks" or cron).

Loads the Flask app, makes sure the database schema is current, then raises any
notifications that are due. A file lock guarantees two overlapping runs cannot send
the same notification twice.
"""
import fcntl
import sys
from datetime import datetime, timezone
from pathlib import Path

from app import app, check_and_send_deadline_notifications, prepare_database


def _lock_path() -> Path:
    instance_dir = Path(app.instance_path)
    instance_dir.mkdir(parents=True, exist_ok=True)
    return instance_dir / "notification_job.lock"


def main() -> int:
    started = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    with _lock_path().open("w") as lock_file:
        try:
            fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            print(f"[{started}] Notification job already running - skipping this run.")
            return 0

        try:
            with app.app_context():
                prepare_database()
                check_and_send_deadline_notifications()
        except Exception as exc:  # pragma: no cover - exercised by the scheduler wrapper tests
            print(f"[{started}] Notification job failed: {exc}", file=sys.stderr)
            return 1

    print(f"[{started}] Notification job completed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
