"""Send a real push notification to ONE user's devices, for testing on a phone.

Run from the project directory (same environment variables as the web app, e.g.
LFS_DB_PATH on PythonAnywhere):

    python send_test_notification.py --email you@example.com
    python send_test_notification.py --email you@example.com --type deadline_48h --competition-id 1 --matchweek 12
    python send_test_notification.py --email you@example.com --list

--type test (default) sends "Last Fan Standing Test" linking to the home page.
The four real categories use the exact title/body/link the scheduler would send for
that competition and matchweek. Nothing is written to NotificationLog, no other user
is contacted and no competition data is changed.
"""
import argparse
import sys

from app import (
    NOTIFICATION_TYPES,
    Competition,
    PushSubscription,
    User,
    app,
    build_notification_message,
    prepare_database,
    send_push_notification,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--email", required=True, help="Email address of the user to notify")
    parser.add_argument("--type", default="test", choices=("test",) + NOTIFICATION_TYPES)
    parser.add_argument("--competition-id", type=int, help="Required for the four real categories")
    parser.add_argument("--matchweek", type=int, help="Required for the four real categories")
    parser.add_argument("--list", action="store_true", help="Only list the user's registered devices")
    args = parser.parse_args(argv)

    with app.app_context():
        prepare_database()

        user = User.query.filter_by(email=args.email.strip().lower()).first()
        if user is None:
            user = User.query.filter(User.email.ilike(args.email.strip())).first()
        if user is None:
            print(f"No user with email {args.email}", file=sys.stderr)
            return 1

        subscriptions = PushSubscription.query.filter_by(user_id=user.id).all()
        print(f"{user.name} <{user.email}> has {len(subscriptions)} registered device(s).")
        for subscription in subscriptions:
            print(f"  - {subscription.endpoint[:70]}... (registered {subscription.created_at:%Y-%m-%d %H:%M} UTC)")
        if args.list:
            return 0
        if not subscriptions:
            print("Nothing to send to - enable notifications on a device first.", file=sys.stderr)
            return 1

        if args.type == "test":
            message = {
                "title": "Last Fan Standing Test ⚽",
                "body": "Push notifications are working!",
                "url": "/",
            }
        else:
            if args.competition_id is None or args.matchweek is None:
                print("--competition-id and --matchweek are required for that type", file=sys.stderr)
                return 2
            competition = Competition.query.get(args.competition_id)
            if competition is None:
                print(f"No competition with id {args.competition_id}", file=sys.stderr)
                return 1
            message = build_notification_message(args.type, competition, args.matchweek)
            if message is None:
                print("That notification would not be sent (e.g. no matchweek follows the one given).", file=sys.stderr)
                return 1

        print(f"Sending: {message['title']} / {message['body']} -> {message['url']}")
        results = send_push_notification(user, message["title"], message["body"], url=message["url"])
        failures = 0
        for result in results:
            label = result["status"].upper()
            detail = f" ({result['error']})" if result["error"] else ""
            print(f"  {label}: {result['endpoint'][:70]}...{detail}")
            if result["status"] != "sent":
                failures += 1
        if failures:
            print(f"{failures} device(s) did not receive the message. Invalid devices have been removed.")
            return 1
        print("Delivered to the push service for every device. Check the phone now.")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
