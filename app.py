from __future__ import annotations

import base64
import csv
import json
import os
from datetime import datetime, timedelta
from pathlib import Path
from time import monotonic
from typing import List

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from flask import Flask, abort, flash, g, jsonify, redirect, render_template, request, send_from_directory, url_for
from flask_login import LoginManager, UserMixin, current_user, login_required, login_user, logout_user
from flask_sqlalchemy import SQLAlchemy
from pywebpush import WebPushException, webpush
from sqlalchemy import func
from werkzeug.security import check_password_hash, generate_password_hash

app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("LFS_SECRET_KEY", "dev-secret-key")

_db_path = os.environ.get("LFS_DB_PATH")
if _db_path:
    app.config["SQLALCHEMY_DATABASE_URI"] = f"sqlite:///{_db_path}"
else:
    _db_name = os.environ.get("LFS_DB_NAME", "last_fan_standing.db")
    app.config["SQLALCHEMY_DATABASE_URI"] = f"sqlite:///{_db_name}"
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
app.config["REMEMBER_COOKIE_DURATION"] = timedelta(days=21)

# Set LFS_NOTIFICATIONS=1 to enable the push-notification feature.
NOTIFICATIONS_ENABLED: bool = os.environ.get("LFS_NOTIFICATIONS", "0") == "1"
NOTIFICATION_POLL_MINUTES = max(1, int(os.environ.get("LFS_NOTIFICATION_POLL_MINUTES", "5")))

db = SQLAlchemy(app)
login_manager = LoginManager(app)
login_manager.login_view = "login"

# --- Web Push (VAPID) configuration -----------------------------------------
# NOTE: iOS Safari only supports Web Push for sites that have been added to the
# Home Screen as an installed web app (iOS 16.4+). In a normal Safari tab, iOS
# does not support the Push API at all. This is a platform limitation, not a
# bug in this app - see the notifications page for the user-facing caveat.
VAPID_CLAIM_EMAIL = "mailto:admin@lastfanstanding.local"


def _vapid_private_key_path() -> Path:
    instance_dir = Path(app.instance_path)
    instance_dir.mkdir(parents=True, exist_ok=True)
    return instance_dir / "vapid_private_key.pem"


def _load_or_create_vapid_private_key() -> ec.EllipticCurvePrivateKey:
    key_path = _vapid_private_key_path()
    if key_path.exists():
        return serialization.load_pem_private_key(key_path.read_bytes(), password=None)

    private_key = ec.generate_private_key(ec.SECP256R1())
    pem_bytes = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    key_path.write_bytes(pem_bytes)
    return private_key


def get_vapid_private_key_pem() -> str:
    private_key = _load_or_create_vapid_private_key()
    return private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("utf-8")


def get_vapid_public_key_b64() -> str:
    public_key = _load_or_create_vapid_private_key().public_key()
    raw_point = public_key.public_bytes(
        encoding=serialization.Encoding.X962,
        format=serialization.PublicFormat.UncompressedPoint,
    )
    return base64.urlsafe_b64encode(raw_point).rstrip(b"=").decode("utf-8")


class User(db.Model, UserMixin):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    phone = db.Column(db.String(30), unique=True, nullable=False)
    password_hash = db.Column(db.String(200), nullable=False)
    is_admin = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def set_password(self, password: str) -> None:
        self.password_hash = generate_password_hash(password)

    def check_password(self, password: str) -> bool:
        return check_password_hash(self.password_hash, password)


class Competition(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    code = db.Column(db.String(20), unique=True, nullable=False)
    admin_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    start_matchweek = db.Column(db.Integer, default=1)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class CompetitionMember(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    competition_id = db.Column(db.Integer, db.ForeignKey("competition.id"), nullable=False, index=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False, index=True)
    lives = db.Column(db.Integer, default=3)
    active = db.Column(db.Boolean, default=True)
    joined_at = db.Column(db.DateTime, default=datetime.utcnow)


class CompetitionPaymentStatus(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    competition_id = db.Column(db.Integer, db.ForeignKey("competition.id"), nullable=False, index=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False, index=True)
    paid = db.Column(db.Boolean, default=False)

    __table_args__ = (
        db.UniqueConstraint("competition_id", "user_id", name="uix_competition_user_payment"),
    )


class Selection(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    competition_id = db.Column(db.Integer, db.ForeignKey("competition.id"), nullable=False, index=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False, index=True)
    matchweek = db.Column(db.Integer, nullable=False)
    team_name = db.Column(db.String(80), nullable=False)
    submitted_at = db.Column(db.DateTime, default=datetime.utcnow)

    __table_args__ = (
        db.UniqueConstraint("competition_id", "user_id", "matchweek", name="uix_user_week"),
    )


class MatchweekOutcome(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    competition_id = db.Column(db.Integer, db.ForeignKey("competition.id"), nullable=False)
    matchweek = db.Column(db.Integer, nullable=False)
    resolved = db.Column(db.Boolean, default=False)

    __table_args__ = (
        db.UniqueConstraint("competition_id", "matchweek", name="uix_competition_week_outcome"),
    )


class MemberMatchweekResolution(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    competition_id = db.Column(db.Integer, db.ForeignKey("competition.id"), nullable=False, index=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False, index=True)
    matchweek = db.Column(db.Integer, nullable=False)
    lost_life = db.Column(db.Boolean, default=False)

    __table_args__ = (
        db.UniqueConstraint("competition_id", "user_id", "matchweek", name="uix_member_week_resolution"),
    )


class FixtureResult(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    matchweek = db.Column(db.Integer, nullable=False)
    home_team = db.Column(db.String(80), nullable=False)
    away_team = db.Column(db.String(80), nullable=False)
    result = db.Column(db.String(20), nullable=True)

    __table_args__ = (
        db.UniqueConstraint("matchweek", "home_team", "away_team", name="uix_fixture_result"),
    )


class PushSubscription(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    endpoint = db.Column(db.String(500), nullable=False, unique=True)
    p256dh = db.Column(db.String(200), nullable=False)
    auth = db.Column(db.String(200), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class NotificationPreference(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False, unique=True)
    deadline_48h = db.Column(db.Boolean, default=False)
    deadline_24h = db.Column(db.Boolean, default=False)
    deadline_passed = db.Column(db.Boolean, default=False)
    results_confirmed = db.Column(db.Boolean, default=False)


class NotificationLog(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False, index=True)
    competition_id = db.Column(db.Integer, db.ForeignKey("competition.id"), nullable=False, index=True)
    matchweek = db.Column(db.Integer, nullable=False)
    notification_type = db.Column(db.String(30), nullable=False)
    sent_at = db.Column(db.DateTime, default=datetime.utcnow)

    __table_args__ = (
        db.UniqueConstraint(
            "user_id", "competition_id", "matchweek", "notification_type",
            name="uix_notification_log",
        ),
    )


@login_manager.user_loader
def load_user(user_id: str):
    return User.query.get(int(user_id))


def get_schedule() -> List[dict]:
    base_time = datetime.utcnow().replace(minute=0, second=0, microsecond=0)
    return [
        {
            "week": 1,
            "kickoff": base_time + timedelta(days=3, hours=2),
            "fixtures": [
                {"home": "Arsenal", "away": "Leicester", "winner": "Arsenal"},
                {"home": "Chelsea", "away": "Brighton", "winner": "Chelsea"},
                {"home": "Tottenham", "away": "Man United", "winner": "Tottenham"},
            ],
        },
        {
            "week": 2,
            "kickoff": base_time + timedelta(days=10, hours=2),
            "fixtures": [
                {"home": "Liverpool", "away": "Everton", "winner": "Liverpool"},
                {"home": "Man City", "away": "Fulham", "winner": "Man City"},
                {"home": "Newcastle", "away": "West Ham", "winner": "Newcastle"},
            ],
        },
        {
            "week": 3,
            "kickoff": base_time + timedelta(days=17, hours=2),
            "fixtures": [
                {"home": "Aston Villa", "away": "Crystal Palace", "winner": "Aston Villa"},
                {"home": "Bournemouth", "away": "Southampton", "winner": "Bournemouth"},
                {"home": "Brentford", "away": "Wolves", "winner": "Brentford"},
            ],
        },
        {
            "week": 4,
            "kickoff": base_time + timedelta(days=24, hours=2),
            "fixtures": [
                {"home": "Nottingham Forest", "away": "Ipswich", "winner": "Nottingham Forest"},
                {"home": "Sunderland", "away": "Burnley", "winner": "Sunderland"},
                {"home": "Sheffield United", "away": "Luton", "winner": "Sheffield United"},
            ],
        },
        {
            "week": 5,
            "kickoff": base_time + timedelta(days=31, hours=2),
            "fixtures": [
                {"home": "Arsenal", "away": "Liverpool", "winner": "Liverpool"},
                {"home": "Chelsea", "away": "Man City", "winner": "Man City"},
                {"home": "Tottenham", "away": "Brighton", "winner": "Brighton"},
            ],
        },
        {
            "week": 6,
            "kickoff": base_time + timedelta(days=38, hours=2),
            "fixtures": [
                {"home": "Man United", "away": "Arsenal", "winner": "Man United"},
                {"home": "Everton", "away": "Chelsea", "winner": "Chelsea"},
                {"home": "Fulham", "away": "Tottenham", "winner": "Tottenham"},
            ],
        },
    ]


SCHEDULE = get_schedule()
FIXTURES_CSV_PATH = Path(__file__).parent / "files" / "202627 EPL fixtures.csv"


def load_fixtures_from_csv() -> List[dict]:
    if not FIXTURES_CSV_PATH.exists():
        return []

    grouped: dict[int, list[dict]] = {}
    with FIXTURES_CSV_PATH.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            matchweek = int(row["Round Number"])
            grouped.setdefault(matchweek, []).append({
                "match_number": row["Match Number"],
                "home_team": row["Home Team"],
                "away_team": row["Away Team"],
                "location": row["Location"],
                "date": row["Date"],
            })

    return [
        {
            "matchweek": matchweek,
            "fixtures": grouped[matchweek],
        }
        for matchweek in sorted(grouped)
    ]


def update_fixture_schedule(match_number: str, new_matchweek: int, new_date: str) -> None:
    """Rewrite the fixtures CSV, updating a single fixture's round number / kickoff date."""
    if not FIXTURES_CSV_PATH.exists():
        raise FileNotFoundError(FIXTURES_CSV_PATH)

    with FIXTURES_CSV_PATH.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        fieldnames = reader.fieldnames
        rows = list(reader)

    found = False
    for row in rows:
        if row["Match Number"] == str(match_number):
            row["Round Number"] = str(new_matchweek)
            row["Date"] = new_date
            found = True
            break

    if not found:
        raise ValueError(f"No fixture found with Match Number {match_number}")

    tmp_path = FIXTURES_CSV_PATH.with_suffix(".tmp")
    with tmp_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    tmp_path.replace(FIXTURES_CSV_PATH)


FIXTURES_BY_MATCHWEEK = load_fixtures_from_csv()
FILES_DIR = Path(__file__).parent / "files"

FIXTURE_INDEX_CACHE = {
    "source_id": None,
    "matchweek_data": {},
    "kickoffs": {},
    "all_matchweeks": [],
}
FIXTURE_ROWS_CACHE = {
    "rows": [],
    "expires_at": 0.0,
    "fingerprint": None,
}
STARTUP_INITIALISED = False


def get_fixture_index() -> dict:
    source_id = id(FIXTURES_BY_MATCHWEEK)
    if FIXTURE_INDEX_CACHE["source_id"] == source_id:
        return FIXTURE_INDEX_CACHE

    matchweek_data: dict[int, dict] = {}
    kickoffs: dict[int, datetime | None] = {}
    all_matchweeks: list[int] = []

    for entry in FIXTURES_BY_MATCHWEEK:
        week = entry["matchweek"]
        all_matchweeks.append(week)
        matchweek_data[week] = {
            "matchweek": week,
            "fixtures": [
                {
                    "home": fixture["home_team"],
                    "away": fixture["away_team"],
                    "winner": None,
                }
                for fixture in entry["fixtures"]
            ],
        }

        kickoff: datetime | None = None
        if entry["fixtures"]:
            first_fixture = entry["fixtures"][0]
            try:
                kickoff = datetime.strptime(first_fixture["date"], "%d/%m/%Y %H:%M")
            except ValueError:
                kickoff = None
        kickoffs[week] = kickoff

    FIXTURE_INDEX_CACHE["source_id"] = source_id
    FIXTURE_INDEX_CACHE["matchweek_data"] = matchweek_data
    FIXTURE_INDEX_CACHE["kickoffs"] = kickoffs
    FIXTURE_INDEX_CACHE["all_matchweeks"] = all_matchweeks
    return FIXTURE_INDEX_CACHE


def get_matchweek_data(matchweek: int) -> dict:
    fixture_index = get_fixture_index()
    return fixture_index["matchweek_data"].get(matchweek, {"matchweek": matchweek, "fixtures": []})


def get_matchweek_kickoff(matchweek: int):
    fixture_index = get_fixture_index()
    return fixture_index["kickoffs"].get(matchweek)


def get_upcoming_matchweek(now: datetime | None = None) -> int:
    current_time = now or datetime.utcnow()
    fixture_index = get_fixture_index()
    all_matchweeks = fixture_index["all_matchweeks"]
    for week in all_matchweeks:
        kickoff = get_matchweek_kickoff(week)
        if kickoff and kickoff > current_time:
            return week
    return all_matchweeks[-1] if all_matchweeks else 1


def normalise_team_name(team_name: str) -> str:
    return (
        team_name.lower()
        .replace("&", "and")
        .replace(" ", "")
        .replace("-", "")
        .replace("'", "")
        .replace(".", "")
    )


def get_team_badge_filename(team_name: str) -> str | None:
    badge_map = {
        "arsenal": "arsenal.png",
        "astonvilla": "astonvilla.png",
        "bournemouth": "bournemouth.png",
        "brentford": "brentford.png",
        "brighton": "brighton.png",
        "brightonandhovealbion": "brighton.png",
        "chelsea": "chelsea.png",
        "coventry": "coventry.png",
        "crystalpalace": "crystalpalace.png",
        "everton": "everton.png",
        "forest": "forest.png",
        "nottinghamforest": "nottmforest.png",
        "nottmforest": "nottmforest.png",
        "fulham": "fulham.png",
        "hull": "hull.png",
        "ipswich": "ipswich.png",
        "leeds": "leeds.png",
        "liverpool": "liverpool.png",
        "mancity": "mancity.png",
        "manutd": "manutd.png",
        "newcastle": "newcastle.png",
        "spurs": "spurs.png",
        "tottenham": "spurs.png",
        "tottenhamhotspur": "spurs.png",
        "sunderland": "sunderland.png",
    }
    return badge_map.get(normalise_team_name(team_name))


def get_team_short_name(team_name: str) -> str:
    short_name_map = {
        "Crystal Palace": "Palace",
        "Nott'm Forest": "Forest",
        "Nottingham Forest": "Forest",
    }
    return short_name_map.get(team_name, team_name)


def get_team_fixtures(
    team_name: str,
    result_lookup: dict[tuple[int, str, str], str] | None = None,
    now: datetime | None = None,
) -> dict:
    result_lookup = result_lookup or {}
    fixtures = []
    for entry in FIXTURES_BY_MATCHWEEK:
        for fixture in entry["fixtures"]:
            if fixture["home_team"] != team_name and fixture["away_team"] != team_name:
                continue

            kickoff = None
            raw_date = fixture.get("date")
            if raw_date:
                try:
                    kickoff = datetime.strptime(raw_date, "%d/%m/%Y %H:%M")
                except ValueError:
                    kickoff = None

            result_value = result_lookup.get((entry["matchweek"], fixture["home_team"], fixture["away_team"]))
            if result_value is None:
                result = FixtureResult.query.filter_by(
                    matchweek=entry["matchweek"],
                    home_team=fixture["home_team"],
                    away_team=fixture["away_team"],
                ).first()
                result_value = result.result if result else None

            result_status = None
            if result_value:
                if result_value == "draw":
                    result_status = "draw"
                elif fixture["home_team"] == team_name and result_value == "home_win":
                    result_status = "win"
                elif fixture["away_team"] == team_name and result_value == "away_win":
                    result_status = "win"
                elif fixture["home_team"] == team_name and result_value == "away_win":
                    result_status = "loss"
                elif fixture["away_team"] == team_name and result_value == "home_win":
                    result_status = "loss"

            fixtures.append({
                "matchweek": entry["matchweek"],
                "home_team": fixture["home_team"],
                "away_team": fixture["away_team"],
                "opponent_team": fixture["away_team"] if fixture["home_team"] == team_name else fixture["home_team"],
                "opponent_short_name": get_team_short_name(
                    fixture["away_team"] if fixture["home_team"] == team_name else fixture["home_team"]
                ),
                "is_home": fixture["home_team"] == team_name,
                "kickoff": kickoff,
                "badge_filename": get_team_badge_filename(fixture["away_team"] if fixture["home_team"] == team_name else fixture["home_team"]),
                "date_label": kickoff.strftime("%a %d %b %H:%M") if kickoff else "TBC",
                "result_status": result_status,
            })

    current_time = now or datetime.utcnow()

    for fixture in fixtures:
        fixture["is_played"] = fixture["kickoff"] is not None and fixture["kickoff"] < current_time
        fixture["is_next"] = False

    next_fixture = next(
        (fixture for fixture in fixtures if fixture["kickoff"] is None or fixture["kickoff"] >= current_time),
        None,
    )
    if next_fixture is not None:
        next_fixture["is_next"] = True

    return {
        "team": team_name,
        "slug": normalise_team_name(team_name),
        "badge_filename": get_team_badge_filename(team_name),
        "fixtures": fixtures,
        "played_count": sum(1 for fixture in fixtures if fixture["is_played"]),
    }


def build_fixture_rows(
    result_lookup: dict[tuple[int, str, str], str] | None = None,
    now: datetime | None = None,
) -> list[dict]:
    teams = sorted({
        fixture["home_team"]
        for entry in FIXTURES_BY_MATCHWEEK
        for fixture in entry["fixtures"]
    } | {
        fixture["away_team"]
        for entry in FIXTURES_BY_MATCHWEEK
        for fixture in entry["fixtures"]
    })

    return [get_team_fixtures(team, result_lookup=result_lookup, now=now) for team in teams]


def _get_fixture_results_signature() -> tuple[int, int]:
    count, max_id = db.session.query(
        func.count(FixtureResult.id),
        func.max(FixtureResult.id),
    ).one()
    return int(count or 0), int(max_id or 0)


def _build_fixture_results_lookup() -> dict[tuple[int, str, str], str]:
    return {
        (item.matchweek, item.home_team, item.away_team): item.result
        for item in FixtureResult.query.all()
        if item.result
    }


def get_cached_fixture_rows(cache_seconds: int = 120) -> list[dict]:
    now_mono = monotonic()
    signature = _get_fixture_results_signature()
    cache_valid = (
        FIXTURE_ROWS_CACHE["rows"]
        and FIXTURE_ROWS_CACHE["fingerprint"] == signature
        and now_mono < FIXTURE_ROWS_CACHE["expires_at"]
    )
    if cache_valid:
        return FIXTURE_ROWS_CACHE["rows"]

    rows = build_fixture_rows(result_lookup=_build_fixture_results_lookup(), now=datetime.utcnow())
    FIXTURE_ROWS_CACHE["rows"] = rows
    FIXTURE_ROWS_CACHE["fingerprint"] = signature
    FIXTURE_ROWS_CACHE["expires_at"] = now_mono + cache_seconds
    return rows


def invalidate_fixture_rows_cache() -> None:
    FIXTURE_ROWS_CACHE["rows"] = []
    FIXTURE_ROWS_CACHE["fingerprint"] = None
    FIXTURE_ROWS_CACHE["expires_at"] = 0.0


def get_competition_schedule(competition: Competition) -> List[dict]:
    start = competition.start_matchweek
    return [entry for entry in FIXTURES_BY_MATCHWEEK if start <= entry["matchweek"] <= start + 2]


def sync_admin_status() -> None:
    target_email = "jamie.clarke20@hotmail.co.uk"
    target_user = User.query.filter_by(email=target_email).first()

    if target_user is not None:
        target_user.is_admin = True
        db.session.add(target_user)
    else:
        for user in User.query.all():
            user.is_admin = True
            db.session.add(user)

    db.session.commit()


def ensure_default_admin_account() -> None:
    default_name = "Jamie C"
    default_email = "jamie.clarke20@hotmail.co.uk"
    default_phone = "07469198329"
    default_password = "charlton20"

    user = User.query.filter_by(email=default_email).first()
    if user is None:
        user = User.query.filter_by(phone=default_phone).first()

    if user is None:
        user = User(name=default_name, email=default_email, phone=default_phone, is_admin=True)
        user.set_password(default_password)
        db.session.add(user)
    else:
        user.name = default_name
        user.email = default_email
        user.phone = default_phone
        user.is_admin = True
        user.set_password(default_password)

    db.session.commit()


def ensure_performance_indexes() -> None:
    """Add indexes for databases created before the index=True columns above existed.

    db.create_all() only adds indexes when creating a brand new table, so an
    already-deployed SQLite database needs these added explicitly. CREATE INDEX IF
    NOT EXISTS makes this a safe no-op once the index is present (including on a
    fresh DB, where db.create_all() already created it under the same name).
    """
    index_statements = [
        "CREATE INDEX IF NOT EXISTS ix_competition_member_competition_id ON competition_member (competition_id)",
        "CREATE INDEX IF NOT EXISTS ix_competition_member_user_id ON competition_member (user_id)",
        "CREATE INDEX IF NOT EXISTS ix_competition_payment_status_competition_id ON competition_payment_status (competition_id)",
        "CREATE INDEX IF NOT EXISTS ix_competition_payment_status_user_id ON competition_payment_status (user_id)",
        "CREATE INDEX IF NOT EXISTS ix_selection_competition_id ON selection (competition_id)",
        "CREATE INDEX IF NOT EXISTS ix_selection_user_id ON selection (user_id)",
        "CREATE INDEX IF NOT EXISTS ix_member_matchweek_resolution_competition_id ON member_matchweek_resolution (competition_id)",
        "CREATE INDEX IF NOT EXISTS ix_member_matchweek_resolution_user_id ON member_matchweek_resolution (user_id)",
        "CREATE INDEX IF NOT EXISTS ix_notification_log_user_id ON notification_log (user_id)",
        "CREATE INDEX IF NOT EXISTS ix_notification_log_competition_id ON notification_log (competition_id)",
    ]
    for statement in index_statements:
        db.session.execute(db.text(statement))
    db.session.commit()


def initialise_admin_if_needed() -> None:
    if User.query.count() == 0:
        admin = User(name="Admin", email="admin@example.com", phone="00000000000", is_admin=True)
        admin.set_password("admin123")
        db.session.add(admin)
        db.session.commit()

    ensure_default_admin_account()
    sync_admin_status()


def get_current_user_competition() -> Competition | None:
    if not current_user.is_authenticated:
        return None

    if hasattr(g, "current_user_competition"):
        return g.current_user_competition

    competition = (
        Competition.query
        .join(CompetitionMember)
        .filter(CompetitionMember.user_id == current_user.id)
        .order_by(Competition.id.desc())
        .first()
    )
    g.current_user_competition = competition
    return competition


def _translate_raw_fixture_result(raw_result: str | None) -> str | None:
    """Translate a stored FixtureResult.result value (home_win/away_win/draw) to home/away/draw."""
    if not raw_result:
        return None
    if raw_result == "home_win":
        return "home"
    if raw_result == "away_win":
        return "away"
    return "draw"


def get_fixture_result(matchweek: int, home_team: str, away_team: str):
    result = FixtureResult.query.filter_by(matchweek=matchweek, home_team=home_team, away_team=away_team).first()
    if not result:
        return None
    return _translate_raw_fixture_result(result.result)


def get_matchweek_teams(matchweek: int) -> list[str]:
    teams = set()
    for fixture in get_matchweek_data(matchweek)["fixtures"]:
        teams.add(fixture["home"])
        teams.add(fixture["away"])
    return sorted(teams)


def assign_missing_picks_for_matchweek(
    competition: Competition,
    matchweek: int,
    members: list["CompetitionMember"],
    selection_by_key: dict[tuple[int, int], str],
    used_teams_by_user: dict[int, set[str]],
) -> None:
    """Auto-assign a fallback pick to any member missing a selection for this matchweek.

    selection_by_key/used_teams_by_user are shared, in-memory lookups covering the whole
    competition (see resolve_completed_matchweeks_for_competition) - they are updated in
    place here so later matchweeks in the same pass see any picks just assigned.
    """
    teams = get_matchweek_teams(matchweek)
    if not teams:
        return

    for member in members:
        if (member.user_id, matchweek) in selection_by_key:
            continue

        used_teams = used_teams_by_user.get(member.user_id, set())
        fallback_team = next((team for team in teams if team not in used_teams), None)
        if fallback_team is None:
            continue

        db.session.add(
            Selection(
                competition_id=competition.id,
                user_id=member.user_id,
                matchweek=matchweek,
                team_name=fallback_team,
            )
        )
        selection_by_key[(member.user_id, matchweek)] = fallback_team
        used_teams_by_user.setdefault(member.user_id, set()).add(fallback_team)


def get_pick_outcome(
    matchweek: int,
    team_name: str | None,
    result_lookup: dict[tuple[int, str, str], str] | None = None,
) -> str | None:
    if not team_name:
        return None

    result_lookup = result_lookup or {}

    for fixture in get_matchweek_data(matchweek)["fixtures"]:
        if team_name not in {fixture["home"], fixture["away"]}:
            continue

        raw_result = result_lookup.get((matchweek, fixture["home"], fixture["away"]))
        if raw_result is not None:
            result = _translate_raw_fixture_result(raw_result)
        else:
            result = get_fixture_result(matchweek, fixture["home"], fixture["away"])

        if result is None:
            return None
        if result == "draw":
            return "loss"
        if result == "home":
            return "win" if team_name == fixture["home"] else "loss"
        if result == "away":
            return "win" if team_name == fixture["away"] else "loss"

    return None


def resolve_matchweek_for_competition(
    competition: Competition,
    matchweek: int,
    members: list["CompetitionMember"],
    results_lookup: dict[tuple[int, str, str], str],
    selection_by_key: dict[tuple[int, int], str],
    resolution_by_key: dict[tuple[int, int], "MemberMatchweekResolution"],
    outcome_by_week: dict[int, "MatchweekOutcome"],
) -> None:
    """Resolve one matchweek using shared, pre-loaded lookups instead of per-fixture/per-member queries."""
    week_data = get_matchweek_data(matchweek)
    if not week_data["fixtures"]:
        return

    all_results_recorded = all(
        results_lookup.get((matchweek, fixture["home"], fixture["away"])) is not None
        for fixture in week_data["fixtures"]
    )

    for member in members:
        team_name = selection_by_key.get((member.user_id, matchweek))
        outcome_for_pick = get_pick_outcome(matchweek, team_name, result_lookup=results_lookup)
        should_lose_life = team_name is not None and outcome_for_pick == "loss"
        resolution = resolution_by_key.get((member.user_id, matchweek))

        if resolution is None:
            resolution = MemberMatchweekResolution(
                competition_id=competition.id,
                user_id=member.user_id,
                matchweek=matchweek,
            )
            db.session.add(resolution)
            resolution_by_key[(member.user_id, matchweek)] = resolution

        resolution.lost_life = should_lose_life

    outcome = outcome_by_week.get(matchweek)
    if outcome is None:
        outcome = MatchweekOutcome(competition_id=competition.id, matchweek=matchweek, resolved=all_results_recorded)
        db.session.add(outcome)
        outcome_by_week[matchweek] = outcome
    else:
        outcome.resolved = all_results_recorded


def resolve_completed_matchweeks_for_competition(competition: Competition, now: datetime | None = None) -> None:
    """Resolve every due matchweek for a competition.

    Loads all the data this needs (fixture results, existing resolutions/outcomes,
    selections) once up front rather than issuing a query per fixture/member/matchweek,
    and skips matchweeks that are already fully resolved for every current member.
    """
    current_time = now or datetime.utcnow()

    members = CompetitionMember.query.filter_by(competition_id=competition.id).all()
    member_user_ids = {member.user_id for member in members}

    results_lookup = _build_fixture_results_lookup()

    outcome_by_week = {
        item.matchweek: item
        for item in MatchweekOutcome.query.filter_by(competition_id=competition.id).all()
    }

    resolution_by_key: dict[tuple[int, int], MemberMatchweekResolution] = {}
    resolved_user_ids_by_week: dict[int, set[int]] = {}
    for item in MemberMatchweekResolution.query.filter_by(competition_id=competition.id).all():
        resolution_by_key[(item.user_id, item.matchweek)] = item
        resolved_user_ids_by_week.setdefault(item.matchweek, set()).add(item.user_id)

    selection_by_key: dict[tuple[int, int], str] = {}
    used_teams_by_user: dict[int, set[str]] = {}
    for item in Selection.query.filter_by(competition_id=competition.id).all():
        selection_by_key[(item.user_id, item.matchweek)] = item.team_name
        used_teams_by_user.setdefault(item.user_id, set()).add(item.team_name)

    for entry in FIXTURES_BY_MATCHWEEK:
        week = entry["matchweek"]
        if week < competition.start_matchweek:
            continue

        week_data = get_matchweek_data(week)
        if not week_data["fixtures"]:
            continue

        all_results_recorded = all(
            results_lookup.get((week, fixture["home"], fixture["away"])) is not None
            for fixture in week_data["fixtures"]
        )
        any_results_recorded = any(
            results_lookup.get((week, fixture["home"], fixture["away"])) is not None
            for fixture in week_data["fixtures"]
        )

        kickoff = get_matchweek_kickoff(week)
        if not any_results_recorded and (kickoff is None or kickoff > current_time):
            continue

        existing_outcome = outcome_by_week.get(week)
        already_resolved = (
            existing_outcome is not None
            and existing_outcome.resolved
            and member_user_ids <= resolved_user_ids_by_week.get(week, set())
        )
        if already_resolved:
            continue

        if kickoff is not None and kickoff <= current_time:
            assign_missing_picks_for_matchweek(competition, week, members, selection_by_key, used_teams_by_user)

        resolve_matchweek_for_competition(
            competition, week, members, results_lookup, selection_by_key, resolution_by_key, outcome_by_week,
        )
        resolved_user_ids_by_week[week] = set(member_user_ids)

    loss_counts = {member.user_id: 0 for member in members}
    for (user_id, _matchweek), resolution in resolution_by_key.items():
        if resolution.lost_life and user_id in loss_counts:
            loss_counts[user_id] += 1

    for member in members:
        member.lives = max(0, 3 - loss_counts.get(member.user_id, 0))
        member.active = member.lives > 0

    db.session.commit()


def resolve_completed_matchweeks_for_competition_if_due(
    competition: Competition,
    now: datetime | None = None,
) -> None:
    resolve_completed_matchweeks_for_competition(competition, now=now)


def send_push_notification(user: "User", title: str, body: str, url: str = "/") -> None:
    subscriptions = PushSubscription.query.filter_by(user_id=user.id).all()
    for subscription in subscriptions:
        try:
            webpush(
                subscription_info={
                    "endpoint": subscription.endpoint,
                    "keys": {"p256dh": subscription.p256dh, "auth": subscription.auth},
                },
                data=json.dumps({"title": title, "body": body, "url": url}),
                vapid_private_key=get_vapid_private_key_pem(),
                vapid_claims={"sub": VAPID_CLAIM_EMAIL},
            )
        except WebPushException as exc:
            status_code = exc.response.status_code if exc.response is not None else None
            if status_code in (404, 410):
                # Subscription is no longer valid (browser unsubscribed or expired).
                db.session.delete(subscription)
                db.session.commit()


def get_notification_target_url() -> str:
    return "/"


def _send_notifications_for_type(
    competition: Competition,
    matchweek: int,
    notification_type: str,
    title: str,
    body: str,
    require_no_pick: bool,
) -> None:
    members = CompetitionMember.query.filter_by(competition_id=competition.id, active=True).all()
    for member in members:
        preference = NotificationPreference.query.filter_by(user_id=member.user_id).first()
        if preference is None or not getattr(preference, notification_type):
            continue

        already_sent = NotificationLog.query.filter_by(
            user_id=member.user_id,
            competition_id=competition.id,
            matchweek=matchweek,
            notification_type=notification_type,
        ).first()
        if already_sent:
            continue

        if require_no_pick:
            has_pick = Selection.query.filter_by(
                competition_id=competition.id,
                user_id=member.user_id,
                matchweek=matchweek,
            ).first()
            if has_pick:
                continue

        user = User.query.get(member.user_id)
        if user is None:
            continue

        send_push_notification(user, title, body, url=get_notification_target_url())
        db.session.add(NotificationLog(
            user_id=member.user_id,
            competition_id=competition.id,
            matchweek=matchweek,
            notification_type=notification_type,
        ))

    db.session.commit()


def check_and_send_deadline_notifications(now: datetime | None = None) -> None:
    """Send opt-in 48hr/24hr/deadline-passed reminders for each competition's
    current matchweek. Safe to call repeatedly (e.g. from a periodic
    scheduler) - NotificationLog prevents duplicate sends."""
    if not NOTIFICATIONS_ENABLED:
        return

    current_time = now or datetime.utcnow()
    for competition in Competition.query.all():
        matchweek = max(competition.start_matchweek, get_upcoming_matchweek(current_time))
        kickoff = get_matchweek_kickoff(matchweek)
        if kickoff is None:
            continue

        deadline = kickoff - timedelta(hours=1)

        if current_time >= deadline - timedelta(hours=48):
            _send_notifications_for_type(
                competition, matchweek, "deadline_48h",
                title="48 hours to go!",
                body=f"You have 48 hours left to submit your Matchweek {matchweek} pick.",
                require_no_pick=True,
            )

        if current_time >= deadline - timedelta(hours=24):
            _send_notifications_for_type(
                competition, matchweek, "deadline_24h",
                title="24 hours to go!",
                body=f"Just 24 hours left to submit your Matchweek {matchweek} pick.",
                require_no_pick=True,
            )

        if current_time >= deadline:
            _send_notifications_for_type(
                competition, matchweek, "deadline_passed",
                title="Selections are in!",
                body=f"The deadline for Matchweek {matchweek} has passed - selections are now viewable.",
                require_no_pick=False,
            )


def notify_results_confirmed(competition: Competition, matchweek: int) -> None:
    if not NOTIFICATIONS_ENABLED:
        return

    _send_notifications_for_type(
        competition, matchweek, "results_confirmed",
        title="Results confirmed",
        body=f"Matchweek {matchweek} results are in - the next matchweek is open.",
        require_no_pick=False,
    )


@app.before_request
def ensure_database_ready() -> None:
    global STARTUP_INITIALISED

    if not NOTIFICATIONS_ENABLED and request.path.startswith("/notifications"):
        abort(404)

    if STARTUP_INITIALISED:
        return

    with app.app_context():
        db.create_all()
        ensure_performance_indexes()
        if not app.config.get("TESTING"):
            initialise_admin_if_needed()

    STARTUP_INITIALISED = True


@app.route("/")
def home():
    fixture_rows = get_cached_fixture_rows()

    competition = None
    current_week = None
    selection = None
    fixture = None
    member = None
    payment_paid = False
    deadline_label = None
    selected_team_badge_filename = None

    competition = get_current_user_competition()
    if competition is not None:
        resolve_completed_matchweeks_for_competition_if_due(competition)
        current_week = max(competition.start_matchweek, get_upcoming_matchweek(datetime.utcnow()))
        selection = Selection.query.filter_by(competition_id=competition.id, user_id=current_user.id, matchweek=current_week).first()
        deadline = get_matchweek_kickoff(current_week)
        deadline_label = deadline - timedelta(hours=1) if deadline else None
        if selection is not None:
            current_week_data = get_matchweek_data(current_week)
            fixture = next((item for item in current_week_data["fixtures"] if item["home"] == selection.team_name or item["away"] == selection.team_name), None)
            selected_team_badge_filename = get_team_badge_filename(selection.team_name)
        member = CompetitionMember.query.filter_by(competition_id=competition.id, user_id=current_user.id).first()
        payment = CompetitionPaymentStatus.query.filter_by(
            competition_id=competition.id,
            user_id=current_user.id,
        ).first()
        payment_paid = payment.paid if payment else False

    return render_template(
        "home.html",
        competition=competition,
        current_week=current_week,
        pick=selection,
        fixture=fixture,
        member=member,
        payment_paid=payment_paid,
        deadline_label=deadline_label,
        selected_team_badge_filename=selected_team_badge_filename,
        now=datetime.utcnow(),
        fixture_rows=fixture_rows,
    )


@app.context_processor
def inject_navigation_context():
    submit_pick_url = None
    view_selections_url = None
    competition = get_current_user_competition()
    if competition is not None:
        matchweek = max(competition.start_matchweek, get_upcoming_matchweek(datetime.utcnow()))
        submit_pick_url = url_for("submit_pick", competition_id=competition.id, matchweek=matchweek)
        view_selections_url = url_for("view_selections", competition_id=competition.id, matchweek=matchweek)
    return {
        "submit_pick_url": submit_pick_url,
        "view_selections_url": view_selections_url,
        "notifications_enabled": NOTIFICATIONS_ENABLED,
    }


@app.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("home"))

    if request.method == "POST":
        email = request.form.get("email", "").strip()
        password = request.form.get("password", "")
        user = User.query.filter_by(email=email).first()
        if user and user.check_password(password):
            login_user(user, remember=True)
            flash("Welcome back, {}".format(user.name), "success")
            return redirect(url_for("home"))
        flash("Invalid email or password", "danger")
    return render_template("login.html")


@app.route("/register", methods=["GET", "POST"])
def register():
    if current_user.is_authenticated:
        return redirect(url_for("home"))

    if request.method == "POST":
        first_name = request.form.get("first_name", "").strip()
        last_initial = request.form.get("last_initial", "").strip()
        legacy_name = request.form.get("name", "").strip()
        legacy_initial = request.form.get("initial", "").strip()

        # Backward compatibility for older cached register forms.
        if not first_name and legacy_name:
            name_parts = legacy_name.split()
            first_name = name_parts[0]
            if not last_initial and len(name_parts) > 1:
                last_initial = name_parts[-1][:3]

        if not last_initial and legacy_initial:
            last_initial = legacy_initial

        email = request.form.get("email", "").strip()
        phone = request.form.get("phone", "").strip()
        password = request.form.get("password", "")
        confirm_password = request.form.get("confirm_password", "")
        display_name = f"{first_name} {last_initial.upper()}".strip()

        if not first_name or not last_initial or not email or not phone or not password:
            flash("All fields are required", "danger")
            return render_template("register.html")
        if len(last_initial) > 3:
            flash("Last initial can be a maximum of 3 characters", "danger")
            return render_template("register.html")
        if "@" not in email:
            flash("Please enter a valid email address", "danger")
            return render_template("register.html")
        if len(phone) < 7:
            flash("Please enter a valid phone number", "danger")
            return render_template("register.html")
        if password != confirm_password:
            flash("Passwords do not match", "danger")
            return render_template("register.html")
        if User.query.filter(func.lower(User.name) == display_name.lower()).first():
            flash("That first name and last initial is already taken. Please try something a little different e.g. a nickname or the first two letters of your last name", "danger")
            return render_template("register.html")
        if User.query.filter_by(email=email).first() or User.query.filter_by(phone=phone).first():
            flash("An account with that email or phone already exists", "danger")
            return render_template("register.html")

        user = User(name=display_name, email=email, phone=phone, is_admin=True)
        user.set_password(password)
        db.session.add(user)
        db.session.commit()
        sync_admin_status()
        login_user(user, remember=True)
        flash("Account created successfully", "success")
        return redirect(url_for("home"))
    return render_template("register.html")


@app.route("/logout")
@login_required
def logout():
    logout_user()
    flash("You have been logged out", "success")
    return redirect(url_for("login"))


@app.route("/forgot_password", methods=["GET", "POST"])
def forgot_password():
    if request.method == "POST":
        identifier = request.form.get("identifier", "").strip()
        user = User.query.filter((User.email == identifier) | (User.phone == identifier)).first()
        if user is None:
            flash("No account matched that email or phone number", "danger")
            return render_template("forgot_password.html")
        user.set_password("reset123")
        db.session.commit()
        flash("Password reset successful. Your temporary password is reset123", "success")
        return redirect(url_for("login"))
    return render_template("forgot_password.html")


@app.route("/competitions")
@login_required
def competitions():
    competitions_list = Competition.query.all()
    member_competitions = [comp for comp in competitions_list if CompetitionMember.query.filter_by(competition_id=comp.id, user_id=current_user.id).first()]
    return render_template("competitions.html", competitions=competitions_list, member_competitions=member_competitions)


@app.route("/competitions/create", methods=["GET", "POST"])
@login_required
def create_competition():
    if not current_user.is_admin:
        flash("Only the admin can create a new competition", "danger")
        return redirect(url_for("competitions"))

    if request.method == "POST":
        name = request.form.get("name", "").strip()
        code = request.form.get("code", "").strip().upper()
        start_matchweek = int(request.form.get("start_matchweek", "1"))

        if not name or not code:
            flash("Please provide a name and code", "danger")
            return render_template("create_competition.html")
        if Competition.query.filter_by(code=code).first():
            flash("That competition code is already in use", "danger")
            return render_template("create_competition.html")

        competition = Competition(name=name, code=code, admin_id=current_user.id, start_matchweek=start_matchweek)
        db.session.add(competition)
        db.session.commit()
        flash("Competition created successfully", "success")
        return redirect(url_for("competitions"))
    return render_template("create_competition.html")


@app.route("/competitions/join", methods=["POST"])
@login_required
def join_competition():
    code = request.form.get("code", "").strip().upper()
    competition = Competition.query.filter_by(code=code).first()
    if not competition:
        flash("No competition matched that code", "danger")
        return redirect(url_for("competitions"))

    existing = CompetitionMember.query.filter_by(competition_id=competition.id, user_id=current_user.id).first()
    if existing:
        flash("You are already a member of this competition", "warning")
        return redirect(url_for("competition_detail", competition_id=competition.id))

    member = CompetitionMember(competition_id=competition.id, user_id=current_user.id, lives=3, active=True)
    db.session.add(member)
    db.session.commit()
    flash("You joined the competition", "success")
    return redirect(url_for("competition_detail", competition_id=competition.id))


@app.route("/competitions/<int:competition_id>")
@login_required
def competition_detail(competition_id: int):
    competition = Competition.query.get_or_404(competition_id)
    member = CompetitionMember.query.filter_by(competition_id=competition.id, user_id=current_user.id).first()
    if not member:
        flash("You must join this competition first", "warning")
        return redirect(url_for("competitions"))

    resolve_completed_matchweeks_for_competition_if_due(competition)
    current_week = max(competition.start_matchweek, get_upcoming_matchweek(datetime.utcnow()))
    members = []
    for item in CompetitionMember.query.filter_by(competition_id=competition.id).all():
        user = User.query.get(item.user_id)
        members.append({"user": user, "member": item})

    return render_template(
        "competition_detail.html",
        competition=competition,
        members=members,
        current_week=current_week,
        can_manage_players=current_user.id == competition.admin_id,
    )


@app.route("/competitions/<int:competition_id>/players")
@login_required
def competition_players_admin(competition_id: int):
    competition = Competition.query.get_or_404(competition_id)
    if current_user.id != competition.admin_id:
        flash("Only the competition owner can view this page", "danger")
        return redirect(url_for("competition_detail", competition_id=competition.id))

    members = []
    for membership in CompetitionMember.query.filter_by(competition_id=competition.id).all():
        user = User.query.get(membership.user_id)
        if user is None:
            continue
        payment = CompetitionPaymentStatus.query.filter_by(
            competition_id=competition.id,
            user_id=user.id,
        ).first()
        members.append({
            "user": user,
            "member": membership,
            "paid": payment.paid if payment else False,
        })

    members.sort(key=lambda item: item["user"].name.lower())
    return render_template("competition_players_admin.html", competition=competition, members=members)


@app.route("/competitions/<int:competition_id>/players/<int:user_id>/toggle_payment", methods=["POST"])
@login_required
def toggle_competition_payment(competition_id: int, user_id: int):
    competition = Competition.query.get_or_404(competition_id)
    if current_user.id != competition.admin_id:
        flash("Only the competition owner can update payment status", "danger")
        return redirect(url_for("competition_detail", competition_id=competition.id))

    member = CompetitionMember.query.filter_by(competition_id=competition.id, user_id=user_id).first()
    if member is None:
        flash("Player is not in this competition", "danger")
        return redirect(url_for("competition_players_admin", competition_id=competition.id))

    payment = CompetitionPaymentStatus.query.filter_by(competition_id=competition.id, user_id=user_id).first()
    if payment is None:
        payment = CompetitionPaymentStatus(competition_id=competition.id, user_id=user_id, paid=True)
        db.session.add(payment)
    else:
        payment.paid = not payment.paid

    db.session.commit()
    flash("Payment status updated", "success")
    return redirect(url_for("competition_players_admin", competition_id=competition.id))


@app.route("/competitions/<int:competition_id>/players/<int:user_id>/remove", methods=["POST"])
@login_required
def remove_competition_player(competition_id: int, user_id: int):
    competition = Competition.query.get_or_404(competition_id)
    if current_user.id != competition.admin_id:
        flash("Only the competition owner can remove players", "danger")
        return redirect(url_for("competition_detail", competition_id=competition.id))

    if user_id == competition.admin_id:
        flash("You cannot remove the competition owner", "danger")
        return redirect(url_for("competition_players_admin", competition_id=competition.id))

    member = CompetitionMember.query.filter_by(competition_id=competition.id, user_id=user_id).first()
    if member is None:
        flash("Player is not in this competition", "danger")
        return redirect(url_for("competition_players_admin", competition_id=competition.id))

    Selection.query.filter_by(competition_id=competition.id, user_id=user_id).delete()
    MemberMatchweekResolution.query.filter_by(competition_id=competition.id, user_id=user_id).delete()
    CompetitionPaymentStatus.query.filter_by(competition_id=competition.id, user_id=user_id).delete()
    NotificationLog.query.filter_by(competition_id=competition.id, user_id=user_id).delete()
    db.session.delete(member)
    db.session.commit()

    flash("Player and their selections were removed from the competition", "success")
    return redirect(url_for("competition_players_admin", competition_id=competition.id))


@app.route("/view_selections/<int:competition_id>/<int:matchweek>")
@login_required
def view_selections(competition_id: int, matchweek: int):
    competition = Competition.query.get_or_404(competition_id)
    member = CompetitionMember.query.filter_by(competition_id=competition.id, user_id=current_user.id).first()
    if not member:
        flash("You must join this competition first", "warning")
        return redirect(url_for("competitions"))

    resolve_completed_matchweeks_for_competition_if_due(competition)

    all_matchweeks = get_fixture_index()["all_matchweeks"]
    if matchweek not in all_matchweeks:
        matchweek = get_upcoming_matchweek(datetime.utcnow())

    matchweek = max(matchweek, competition.start_matchweek)
    week_data = get_matchweek_data(matchweek)
    kickoff_time = get_matchweek_kickoff(matchweek)
    deadline = kickoff_time - timedelta(hours=1) if kickoff_time else None
    now = datetime.utcnow()
    current_week_revealed = deadline is not None and now >= deadline
    weeks = [week for week in all_matchweeks if competition.start_matchweek <= week <= matchweek]

    week_revealed_map: dict[int, bool] = {}
    for week in weeks:
        week_kickoff = get_matchweek_kickoff(week)
        week_deadline = week_kickoff - timedelta(hours=1) if week_kickoff else None
        week_revealed_map[week] = week_deadline is not None and now >= week_deadline

    selections = Selection.query.filter(
        Selection.competition_id == competition.id,
        Selection.matchweek.in_(weeks),
    ).all()
    selection_map = {(item.user_id, item.matchweek): item for item in selections}

    fixture_result_lookup = {
        (item.matchweek, item.home_team, item.away_team): item.result
        for item in FixtureResult.query.filter(FixtureResult.matchweek.in_(weeks)).all()
        if item.result
    }

    fixtures_by_week_and_team: dict[int, dict[str, str]] = {}
    for week in weeks:
        fixtures_by_week_and_team[week] = {}
        for fixture in get_matchweek_data(week)["fixtures"]:
            label = f"{fixture['home']} vs {fixture['away']}"
            fixtures_by_week_and_team[week][fixture["home"]] = label
            fixtures_by_week_and_team[week][fixture["away"]] = label

    players = []
    memberships = CompetitionMember.query.filter_by(competition_id=competition.id).all()
    user_ids = [membership.user_id for membership in memberships]
    users_by_id = {
        user.id: user
        for user in User.query.filter(User.id.in_(user_ids)).all()
    } if user_ids else {}

    for membership in memberships:
        user = users_by_id.get(membership.user_id)
        if user is None:
            continue

        picks = []
        for week in weeks:
            selection = selection_map.get((membership.user_id, week))
            team_name = selection.team_name if selection else None
            outcome = get_pick_outcome(week, team_name, result_lookup=fixture_result_lookup)

            if not week_revealed_map.get(week, False):
                picks.append({
                    "display": "status",
                    "has_submitted": selection is not None,
                })
                continue

            picks.append({
                "display": "team",
                "team_name": team_name,
                "team_badge_filename": get_team_badge_filename(team_name) if team_name else None,
                "fixture_label": fixtures_by_week_and_team.get(week, {}).get(team_name),
                "outcome": outcome,
            })

        players.append({
            "name": user.name,
            "lives": membership.lives,
            "picks": picks,
        })

    if not players:
        players.append({
            "name": current_user.name,
            "lives": member.lives,
            "picks": [],
        })

    players.sort(key=lambda item: item["name"].lower())

    return render_template(
        "view_selections.html",
        competition=competition,
        matchweek=matchweek,
        week_data=week_data,
        players=players,
        weeks=weeks,
        deadline=deadline,
        current_week_revealed=current_week_revealed,
    )


@app.route("/submit_pick/<int:competition_id>/<int:matchweek>", methods=["GET", "POST"])
@login_required
def submit_pick(competition_id: int, matchweek: int):
    competition = Competition.query.get_or_404(competition_id)
    member = CompetitionMember.query.filter_by(competition_id=competition.id, user_id=current_user.id).first()
    if not member:
        flash("You must join this competition first", "warning")
        return redirect(url_for("competitions"))

    resolve_completed_matchweeks_for_competition_if_due(competition)
    week_data = get_matchweek_data(matchweek)
    kickoff = get_matchweek_kickoff(matchweek)
    picks_locked = kickoff is not None and datetime.utcnow() >= kickoff
    pick_fixtures = []
    for fixture in week_data["fixtures"]:
        pick_fixtures.append({
            "home": fixture["home"],
            "away": fixture["away"],
            "home_badge_filename": get_team_badge_filename(fixture["home"]),
            "away_badge_filename": get_team_badge_filename(fixture["away"]),
        })

    existing_selection = Selection.query.filter_by(
        competition_id=competition.id,
        user_id=current_user.id,
        matchweek=matchweek,
    ).first()
    all_user_selections = Selection.query.filter_by(competition_id=competition.id, user_id=current_user.id).all()
    used_teams = {item.team_name for item in all_user_selections}
    blocked_teams = {
        team_name
        for team_name in used_teams
        if existing_selection is None or existing_selection.team_name != team_name
    }

    available_matchweeks = [
        entry["matchweek"]
        for entry in FIXTURES_BY_MATCHWEEK
        if entry["matchweek"] >= competition.start_matchweek
    ]
    current_index = available_matchweeks.index(matchweek) if matchweek in available_matchweeks else 0
    previous_matchweek = available_matchweeks[current_index - 1] if current_index > 0 else None
    next_matchweek = available_matchweeks[current_index + 1] if current_index < len(available_matchweeks) - 1 else None

    if request.method == "POST":
        if picks_locked:
            flash("This matchweek has already started. Picks can no longer be changed.", "danger")
            return redirect(url_for("submit_pick", competition_id=competition.id, matchweek=matchweek))

        team = request.form.get("team", "").strip()
        if not team:
            flash("Please choose a team", "danger")
            return render_template(
                "submit_pick.html",
                competition=competition,
                matchweek=matchweek,
                week_data=week_data,
                pick_fixtures=pick_fixtures,
                existing_selection=existing_selection,
                blocked_teams=blocked_teams,
                previous_matchweek=previous_matchweek,
                next_matchweek=next_matchweek,
                available_matchweeks=available_matchweeks,
                picks_locked=picks_locked,
            )

        if team in blocked_teams:
            flash("That team has already been used in another matchweek", "danger")
            return render_template(
                "submit_pick.html",
                competition=competition,
                matchweek=matchweek,
                week_data=week_data,
                pick_fixtures=pick_fixtures,
                existing_selection=existing_selection,
                blocked_teams=blocked_teams,
                previous_matchweek=previous_matchweek,
                next_matchweek=next_matchweek,
                available_matchweeks=available_matchweeks,
                picks_locked=picks_locked,
            )

        selection = Selection.query.filter_by(
            competition_id=competition.id,
            user_id=current_user.id,
            matchweek=matchweek,
        ).first()
        if selection is None:
            selection = Selection(
                competition_id=competition.id,
                user_id=current_user.id,
                matchweek=matchweek,
                team_name=team,
            )
            db.session.add(selection)
        else:
            selection.team_name = team
        db.session.commit()
        flash("Pick saved", "success")
        return redirect(url_for("submit_pick", competition_id=competition.id, matchweek=matchweek))

    return render_template(
        "submit_pick.html",
        competition=competition,
        matchweek=matchweek,
        week_data=week_data,
        pick_fixtures=pick_fixtures,
        existing_selection=existing_selection,
        blocked_teams=blocked_teams,
        previous_matchweek=previous_matchweek,
        next_matchweek=next_matchweek,
        available_matchweeks=available_matchweeks,
        picks_locked=picks_locked,
    )


@app.route("/files/<path:filename>")
def files_asset(filename: str):
    return send_from_directory(FILES_DIR, filename)


@app.route("/fixtures")
def fixtures():
    return render_template("fixtures.html", fixture_rows=get_cached_fixture_rows())


@app.route("/rules-and-prizes")
def rules_and_prizes():
    return render_template("rules_prizes.html")


@app.route("/admin/results", methods=["GET", "POST"])
@login_required
def admin_results():
    if not current_user.is_admin:
        flash("Only the admin can manage fixture results", "danger")
        return redirect(url_for("home"))

    if request.method == "POST":
        matchweek = int(request.form.get("matchweek", "1"))
        home_team = request.form.get("home_team", "").strip()
        away_team = request.form.get("away_team", "").strip()
        result = request.form.get("result", "").strip()

        if result:
            existing = FixtureResult.query.filter_by(matchweek=matchweek, home_team=home_team, away_team=away_team).first()
            if existing is None:
                existing = FixtureResult(matchweek=matchweek, home_team=home_team, away_team=away_team)
                db.session.add(existing)
            existing.result = result
        else:
            FixtureResult.query.filter_by(matchweek=matchweek, home_team=home_team, away_team=away_team).delete()

        # Any result edit can change outcomes for that matchweek, so force a re-resolve.
        MatchweekOutcome.query.filter_by(matchweek=matchweek).update({"resolved": False})

        # Make sure recalculation sees the newly saved fixture result in this same request.
        db.session.flush()

        # Recalculate affected competitions immediately so lives update as soon as results are saved.
        affected_competitions = Competition.query.filter(
            Competition.start_matchweek <= matchweek,
        ).all()
        for affected_competition in affected_competitions:
            resolve_completed_matchweeks_for_competition(affected_competition)
            outcome = MatchweekOutcome.query.filter_by(
                competition_id=affected_competition.id,
                matchweek=matchweek,
            ).first()
            if outcome is not None and outcome.resolved:
                notify_results_confirmed(affected_competition, matchweek)

        db.session.commit()
        invalidate_fixture_rows_cache()

        flash("Fixture result saved", "success")
        return redirect(url_for("admin_results"))

    fixtures_by_week = []
    for entry in FIXTURES_BY_MATCHWEEK:
        current_entries = []
        for fixture in entry["fixtures"]:
            result = FixtureResult.query.filter_by(
                matchweek=entry["matchweek"],
                home_team=fixture["home_team"],
                away_team=fixture["away_team"],
            ).first()
            current_entries.append({
                "home_team": fixture["home_team"],
                "away_team": fixture["away_team"],
                "result": result.result if result else None,
            })
        fixtures_by_week.append({"matchweek": entry["matchweek"], "fixtures": current_entries})

    return render_template("admin_results.html", fixtures_by_week=fixtures_by_week)


@app.route("/admin/fixtures", methods=["GET", "POST"])
@login_required
def admin_fixtures():
    if not current_user.is_admin:
        flash("Only the admin can edit the fixture schedule", "danger")
        return redirect(url_for("home"))

    global FIXTURES_BY_MATCHWEEK

    if request.method == "POST":
        match_number = request.form.get("match_number", "").strip()
        new_matchweek_raw = request.form.get("matchweek", "").strip()
        new_date = request.form.get("date", "").strip()

        try:
            new_matchweek = int(new_matchweek_raw)
        except ValueError:
            flash("Matchweek must be a whole number", "danger")
            return redirect(url_for("admin_fixtures"))

        try:
            datetime.strptime(new_date, "%d/%m/%Y %H:%M")
        except ValueError:
            flash("Date must be in the format DD/MM/YYYY HH:MM, e.g. 17/08/2026 15:00", "danger")
            return redirect(url_for("admin_fixtures"))

        try:
            update_fixture_schedule(match_number, new_matchweek, new_date)
        except (FileNotFoundError, ValueError) as error:
            flash(str(error), "danger")
            return redirect(url_for("admin_fixtures"))

        FIXTURES_BY_MATCHWEEK = load_fixtures_from_csv()
        invalidate_fixture_rows_cache()

        flash("Fixture schedule updated", "success")
        return redirect(url_for("admin_fixtures"))

    return render_template("admin_fixtures.html", fixtures_by_week=FIXTURES_BY_MATCHWEEK)


def build_form_table() -> list[dict]:
    fixture_teams = {
        fixture["home_team"]
        for entry in FIXTURES_BY_MATCHWEEK
        for fixture in entry["fixtures"]
    } | {
        fixture["away_team"]
        for entry in FIXTURES_BY_MATCHWEEK
        for fixture in entry["fixtures"]
    }
    result_teams = {
        result.home_team
        for result in FixtureResult.query.all()
    } | {
        result.away_team
        for result in FixtureResult.query.all()
    }
    teams = sorted(fixture_teams | result_teams)

    all_results = FixtureResult.query.order_by(FixtureResult.matchweek.asc(), FixtureResult.id.asc()).all()

    table = []
    for team in teams:
        results = []
        seen_keys = set()

        for entry in FIXTURES_BY_MATCHWEEK:
            for fixture in entry["fixtures"]:
                if fixture["home_team"] != team and fixture["away_team"] != team:
                    continue

                result = FixtureResult.query.filter_by(
                    matchweek=entry["matchweek"],
                    home_team=fixture["home_team"],
                    away_team=fixture["away_team"],
                ).first()
                if not result or not result.result:
                    continue

                key = (entry["matchweek"], fixture["home_team"], fixture["away_team"])
                if key in seen_keys:
                    continue
                seen_keys.add(key)

                if fixture["home_team"] == team:
                    if result.result == "home_win":
                        label, css_class = "W", "win"
                        opponent = fixture["away_team"]
                    elif result.result == "away_win":
                        label, css_class = "L", "loss"
                        opponent = fixture["away_team"]
                    else:
                        label, css_class = "D", "draw"
                        opponent = fixture["away_team"]
                else:
                    if result.result == "away_win":
                        label, css_class = "W", "win"
                        opponent = fixture["home_team"]
                    elif result.result == "home_win":
                        label, css_class = "L", "loss"
                        opponent = fixture["home_team"]
                    else:
                        label, css_class = "D", "draw"
                        opponent = fixture["home_team"]

                results.append({
                    "matchweek": entry["matchweek"],
                    "label": label,
                    "class": css_class,
                    "opponent": f"vs {opponent}",
                })

        for result in all_results:
            if team not in {result.home_team, result.away_team}:
                continue

            key = (result.matchweek, result.home_team, result.away_team)
            if key in seen_keys:
                continue
            seen_keys.add(key)

            if result.home_team == team:
                if result.result == "home_win":
                    label, css_class = "W", "win"
                    opponent = result.away_team
                elif result.result == "away_win":
                    label, css_class = "L", "loss"
                    opponent = result.away_team
                else:
                    label, css_class = "D", "draw"
                    opponent = result.away_team
            else:
                if result.result == "away_win":
                    label, css_class = "W", "win"
                    opponent = result.home_team
                elif result.result == "home_win":
                    label, css_class = "L", "loss"
                    opponent = result.home_team
                else:
                    label, css_class = "D", "draw"
                    opponent = result.home_team

            results.append({
                "matchweek": result.matchweek,
                "label": label,
                "class": css_class,
                "opponent": f"vs {opponent}",
            })

        recent_results = sorted(results, key=lambda item: item["matchweek"])[-6:]
        slots = [None] * 6
        for index, result in enumerate(recent_results):
            slots[5 - (len(recent_results) - 1 - index)] = result

        table.append({"team": team, "slots": slots})

    return table


@app.route("/league_table")
@login_required
def league_table():
    table = build_form_table()
    return render_template("league_table.html", table=table)


@app.route("/notifications", methods=["GET", "POST"])
@login_required
def notifications():
    if not NOTIFICATIONS_ENABLED:
        abort(404)

    preference = NotificationPreference.query.filter_by(user_id=current_user.id).first()
    if preference is None:
        preference = NotificationPreference(user_id=current_user.id)
        db.session.add(preference)
        db.session.commit()

    if request.method == "POST":
        preference.deadline_48h = request.form.get("deadline_48h") == "on"
        preference.deadline_24h = request.form.get("deadline_24h") == "on"
        preference.deadline_passed = request.form.get("deadline_passed") == "on"
        preference.results_confirmed = request.form.get("results_confirmed") == "on"
        db.session.commit()
        flash("Notification preferences saved", "success")
        return redirect(url_for("notifications"))

    return render_template(
        "notifications.html",
        preference=preference,
        vapid_public_key=get_vapid_public_key_b64(),
    )


@app.route("/notifications/subscribe", methods=["POST"])
@login_required
def notifications_subscribe():
    if not NOTIFICATIONS_ENABLED:
        abort(404)

    data = request.get_json(silent=True) or {}
    endpoint = data.get("endpoint")
    keys = data.get("keys") or {}
    p256dh = keys.get("p256dh")
    auth = keys.get("auth")
    if not endpoint or not p256dh or not auth:
        return jsonify({"error": "Invalid push subscription"}), 400

    subscription = PushSubscription.query.filter_by(endpoint=endpoint).first()
    if subscription is None:
        subscription = PushSubscription(
            user_id=current_user.id,
            endpoint=endpoint,
            p256dh=p256dh,
            auth=auth,
        )
        db.session.add(subscription)
    else:
        subscription.user_id = current_user.id
        subscription.p256dh = p256dh
        subscription.auth = auth
    db.session.commit()
    return jsonify({"status": "ok"})


@app.route("/notifications/vapid-public-key")
def notifications_vapid_public_key():
    if not NOTIFICATIONS_ENABLED:
        abort(404)

    return jsonify({"publicKey": get_vapid_public_key_b64()})


if __name__ == "__main__":
    with app.app_context():
        db.create_all()

    from apscheduler.schedulers.background import BackgroundScheduler

    def run_deadline_notification_check() -> None:
        with app.app_context():
            check_and_send_deadline_notifications()

    if NOTIFICATIONS_ENABLED:
        scheduler = BackgroundScheduler(daemon=True)
        scheduler.add_job(
            run_deadline_notification_check,
            "interval",
            minutes=NOTIFICATION_POLL_MINUTES,
            id="deadline_notifications",
        )
        scheduler.start()

    _port = int(os.environ.get("LFS_PORT", "5000"))
    app.run(host="0.0.0.0", port=_port, debug=True, use_reloader=False)
