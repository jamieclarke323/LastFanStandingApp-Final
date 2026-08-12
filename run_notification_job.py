import sys

from app import app, check_and_send_deadline_notifications


def main() -> int:
    try:
        with app.app_context():
            check_and_send_deadline_notifications()
    except Exception as exc:  # pragma: no cover - exercised by the scheduler wrapper tests
        print(f"Notification job failed: {exc}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
