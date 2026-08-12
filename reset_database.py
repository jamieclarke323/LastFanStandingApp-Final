"""One-off maintenance script: wipe all competitions and non-admin players.

Keeps every user with is_admin=True (e.g. the 123@123.com account) but deletes:
  - every other User
  - every Competition, and all rows in tables that reference a competition
    (CompetitionMember, CompetitionPaymentStatus, Selection, MatchweekOutcome,
    MemberMatchweekResolution, NotificationLog)
  - PushSubscription / NotificationPreference rows belonging to deleted users

FixtureResult (recorded match scores) is left untouched since it isn't tied to
a specific competition or player.

Uses the same LFS_DB_NAME / LFS_DB_PATH env vars as app.py, so point it at the
right database, e.g.:

    LFS_DB_NAME=last_fan_standing.db python3 reset_database.py

Run with --yes to skip the confirmation prompt (e.g. in a non-interactive
PythonAnywhere console).
"""
from __future__ import annotations

import sys

from app import (
    Competition,
    CompetitionMember,
    CompetitionPaymentStatus,
    MatchweekOutcome,
    MemberMatchweekResolution,
    NotificationLog,
    NotificationPreference,
    PushSubscription,
    Selection,
    User,
    app,
    db,
)


def run(skip_confirm: bool = False) -> None:
    with app.app_context():
        db.create_all()

        admin_users = User.query.filter_by(is_admin=True).all()
        other_users = User.query.filter_by(is_admin=False).all()
        competitions = Competition.query.all()

        print(f"Database: {app.config['SQLALCHEMY_DATABASE_URI']}")
        print(f"Admin users kept: {[u.email for u in admin_users]}")
        print(f"Other users to delete: {[u.email for u in other_users]}")
        print(f"Competitions to delete: {[c.name for c in competitions]}")

        if not other_users and not competitions:
            print("Nothing to wipe - database already only has admin users and no competitions.")
            return

        if not skip_confirm:
            answer = input("Type 'wipe' to confirm this irreversible deletion: ")
            if answer.strip().lower() != "wipe":
                print("Aborted - no changes made.")
                return

        other_user_ids = [u.id for u in other_users]
        competition_ids = [c.id for c in competitions]

        if competition_ids:
            CompetitionMember.query.filter(CompetitionMember.competition_id.in_(competition_ids)).delete(synchronize_session=False)
            CompetitionPaymentStatus.query.filter(CompetitionPaymentStatus.competition_id.in_(competition_ids)).delete(synchronize_session=False)
            Selection.query.filter(Selection.competition_id.in_(competition_ids)).delete(synchronize_session=False)
            MatchweekOutcome.query.filter(MatchweekOutcome.competition_id.in_(competition_ids)).delete(synchronize_session=False)
            MemberMatchweekResolution.query.filter(MemberMatchweekResolution.competition_id.in_(competition_ids)).delete(synchronize_session=False)
            NotificationLog.query.filter(NotificationLog.competition_id.in_(competition_ids)).delete(synchronize_session=False)
            Competition.query.filter(Competition.id.in_(competition_ids)).delete(synchronize_session=False)

        if other_user_ids:
            PushSubscription.query.filter(PushSubscription.user_id.in_(other_user_ids)).delete(synchronize_session=False)
            NotificationPreference.query.filter(NotificationPreference.user_id.in_(other_user_ids)).delete(synchronize_session=False)
            User.query.filter(User.id.in_(other_user_ids)).delete(synchronize_session=False)

        db.session.commit()
        print("Done. Remaining users:", [u.email for u in User.query.all()])
        print("Remaining competitions:", Competition.query.count())


if __name__ == "__main__":
    run(skip_confirm="--yes" in sys.argv)
