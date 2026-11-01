"""Recipient eligibility rules for the four push notification categories.

Players are eliminated here the same way they are in the real game - by recording
fixture results against losing picks - so every test exercises the app's own
authoritative lives calculation rather than hand-setting CompetitionMember.lives.
"""
from datetime import datetime
from itertools import count

import pytest

import app as app_module
from app import (
    Competition,
    CompetitionMember,
    FixtureResult,
    MatchweekOutcome,
    NotificationLog,
    NotificationPreference,
    PushSubscription,
    Selection,
    User,
    app,
    check_and_send_deadline_notifications,
    db,
    notify_results_confirmed,
    resolve_completed_matchweeks_for_competition,
)

# Matchweeks 1-3 are in the past (used to eliminate players); 4 is the upcoming week the
# deadline reminders target; 5 exists so "new matchweek open" has somewhere to point.
# Kickoffs are UK winter time, so 12:00 UK == 12:00 UTC and the MW4 deadline is
# kickoff - 1h = 10/01/2030 11:00 UTC.
FIXTURES = [
    {'matchweek': 1, 'fixtures': [{'home_team': 'Arsenal', 'away_team': 'Coventry', 'location': 'X', 'date': '01/01/2026 12:00'}]},
    {'matchweek': 2, 'fixtures': [{'home_team': 'Chelsea', 'away_team': 'Everton', 'location': 'X', 'date': '08/01/2026 12:00'}]},
    {'matchweek': 3, 'fixtures': [{'home_team': 'Liverpool', 'away_team': 'Fulham', 'location': 'X', 'date': '15/01/2026 12:00'}]},
    {'matchweek': 4, 'fixtures': [{'home_team': 'Spurs', 'away_team': 'Leeds', 'location': 'X', 'date': '10/01/2030 12:00'}]},
    {'matchweek': 5, 'fixtures': [{'home_team': 'Man City', 'away_team': 'Sunderland', 'location': 'X', 'date': '17/01/2030 12:00'}]},
]
UPCOMING_MATCHWEEK = 4
PAST_WEEKS = [(1, 'Arsenal', 'Coventry'), (2, 'Chelsea', 'Everton'), (3, 'Liverpool', 'Fulham')]

BEFORE_ANY_WINDOW = datetime(2030, 1, 7, 11, 30)
IN_48H_WINDOW = datetime(2030, 1, 8, 11, 30)
IN_24H_WINDOW = datetime(2030, 1, 9, 11, 30)
AFTER_DEADLINE = datetime(2030, 1, 10, 11, 30)

_sequence = count(1)


@pytest.fixture
def ctx(monkeypatch):
    app.config.update(TESTING=True, SQLALCHEMY_DATABASE_URI='sqlite:///:memory:')
    monkeypatch.setattr(app_module, 'NOTIFICATIONS_ENABLED', True)
    monkeypatch.setattr(app_module, 'FIXTURES_BY_MATCHWEEK', FIXTURES)
    sent = []
    monkeypatch.setattr(app_module, 'webpush', lambda **kwargs: sent.append(kwargs))
    with app.app_context():
        db.create_all()
        yield sent
        db.session.remove()
        db.drop_all()


def _make_user(email=None):
    n = next(_sequence)
    user = User(name=f'Player {n}', email=email or f'player{n}@example.com', phone=f'0700000{n:04d}')
    user.set_password('password123')
    db.session.add(user)
    db.session.commit()
    return user


def _make_competition(admin, name):
    competition = Competition(name=name, code=f'CODE{next(_sequence)}', admin_id=admin.id, start_matchweek=1)
    db.session.add(competition)
    db.session.commit()
    return competition


def _join(user, competition):
    member = CompetitionMember(competition_id=competition.id, user_id=user.id, lives=3, active=True)
    db.session.add(member)
    db.session.commit()
    return member


def _subscribe(user, **prefs):
    db.session.add(PushSubscription(
        user_id=user.id,
        endpoint=f'https://push.example.com/{user.id}-{next(_sequence)}',
        p256dh='p256dh',
        auth='auth',
    ))
    db.session.add(NotificationPreference(user_id=user.id, **prefs))
    db.session.commit()


def _record_past_results():
    """Home win in every past matchweek, so anyone who picked the away team lost a life."""
    for week, home, away in PAST_WEEKS:
        if FixtureResult.query.filter_by(matchweek=week, home_team=home, away_team=away).first() is None:
            db.session.add(FixtureResult(matchweek=week, home_team=home, away_team=away, result='home_win'))
    db.session.commit()


def _pick_past_weeks(user, competition, losing: bool, weeks=PAST_WEEKS):
    for week, home, away in weeks:
        db.session.add(Selection(
            competition_id=competition.id, user_id=user.id, matchweek=week, team_name=away if losing else home,
        ))
    db.session.commit()


def _settle(*competitions):
    """Record the past results and run the app's own lives calculation.

    Must be called after every member's past-week picks are in place, because resolving
    auto-assigns fallback picks for anyone missing one (exactly as the live app does).
    """
    _record_past_results()
    for competition in competitions:
        resolve_completed_matchweeks_for_competition(competition)


def _eliminate(user, competition):
    """Lose three lives via the real mechanism: losing picks + recorded results."""
    _pick_past_weeks(user, competition, losing=True)
    _settle(competition)


def _keep_alive(user, competition):
    _pick_past_weeks(user, competition, losing=False)
    _settle(competition)


def _pick_upcoming(user, competition, team='Spurs'):
    db.session.add(Selection(
        competition_id=competition.id, user_id=user.id, matchweek=UPCOMING_MATCHWEEK, team_name=team,
    ))
    db.session.commit()


def _lives(user, competition):
    return CompetitionMember.query.filter_by(competition_id=competition.id, user_id=user.id).first().lives


def _logged_types(user, competition):
    return sorted(
        item.notification_type
        for item in NotificationLog.query.filter_by(user_id=user.id, competition_id=competition.id).all()
    )


def _payload(call):
    import json
    return json.loads(call['data'])


# 1. A player with 1 life and no selection receives an opted-in 48h or 24h reminder.
def test_player_with_lives_and_no_pick_receives_48h_reminder(ctx):
    sent = ctx
    user = _make_user()
    competition = _make_competition(user, 'Saturday League')
    _join(user, competition)
    _subscribe(user, deadline_48h=True)

    check_and_send_deadline_notifications(now=IN_48H_WINDOW)

    assert len(sent) == 1
    assert _payload(sent[0])['url'] == f'/submit_pick/{competition.id}/{UPCOMING_MATCHWEEK}'
    assert 'Saturday League' in _payload(sent[0])['body']
    assert _logged_types(user, competition) == ['deadline_48h']


def test_player_with_one_life_and_no_pick_receives_24h_reminder(ctx):
    sent = ctx
    user = _make_user()
    competition = _make_competition(user, 'One Life League')
    _join(user, competition)
    # Lose two of three lives through recorded results.
    _pick_past_weeks(user, competition, losing=True, weeks=PAST_WEEKS[:2])
    _pick_past_weeks(user, competition, losing=False, weeks=PAST_WEEKS[2:])
    _settle(competition)
    assert _lives(user, competition) == 1
    _subscribe(user, deadline_24h=True)

    check_and_send_deadline_notifications(now=IN_24H_WINDOW)

    assert len(sent) == 1
    assert _logged_types(user, competition) == ['deadline_24h']


# 2. A player with 1 life who has already selected receives neither reminder.
def test_player_who_already_selected_receives_neither_reminder(ctx):
    sent = ctx
    user = _make_user()
    competition = _make_competition(user, 'Picked League')
    _join(user, competition)
    _subscribe(user, deadline_48h=True, deadline_24h=True)
    _pick_upcoming(user, competition)

    check_and_send_deadline_notifications(now=IN_48H_WINDOW)
    check_and_send_deadline_notifications(now=IN_24H_WINDOW)

    assert sent == []
    assert _logged_types(user, competition) == []


# 3. A player with 0 lives receives neither deadline reminder.
def test_eliminated_player_receives_neither_deadline_reminder(ctx):
    sent = ctx
    user = _make_user()
    competition = _make_competition(user, 'Out League')
    _join(user, competition)
    _eliminate(user, competition)
    assert _lives(user, competition) == 0
    _subscribe(user, deadline_48h=True, deadline_24h=True)

    check_and_send_deadline_notifications(now=IN_48H_WINDOW)
    check_and_send_deadline_notifications(now=IN_24H_WINDOW)

    assert sent == []


# 4. A player with 0 lives can receive the Selections Viewable notification.
def test_eliminated_player_receives_selections_viewable(ctx):
    sent = ctx
    user = _make_user()
    competition = _make_competition(user, 'Viewable League')
    _join(user, competition)
    _eliminate(user, competition)
    _subscribe(user, deadline_passed=True)

    check_and_send_deadline_notifications(now=AFTER_DEADLINE)

    assert len(sent) == 1
    assert _payload(sent[0])['url'] == f'/view_selections/{competition.id}/{UPCOMING_MATCHWEEK}'
    assert _logged_types(user, competition) == ['deadline_passed']


def test_selections_viewable_not_sent_before_deadline(ctx):
    sent = ctx
    user = _make_user()
    competition = _make_competition(user, 'Early League')
    _join(user, competition)
    _subscribe(user, deadline_passed=True)

    check_and_send_deadline_notifications(now=IN_24H_WINDOW)

    assert sent == []


# 5. A player with 0 lives does not receive the New Gameweek Open notification.
def test_eliminated_player_does_not_receive_new_gameweek_open(ctx):
    sent = ctx
    alive = _make_user()
    eliminated = _make_user()
    competition = _make_competition(alive, 'Open League')
    _join(alive, competition)
    _join(eliminated, competition)
    _pick_past_weeks(alive, competition, losing=False)
    _pick_past_weeks(eliminated, competition, losing=True)
    _settle(competition)
    assert _lives(alive, competition) == 3
    assert _lives(eliminated, competition) == 0
    _subscribe(alive, results_confirmed=True)
    _subscribe(eliminated, results_confirmed=True)

    notify_results_confirmed(competition, 3)

    assert len(sent) == 1
    assert _payload(sent[0])['url'] == f'/submit_pick/{competition.id}/4'
    assert _logged_types(alive, competition) == ['results_confirmed']
    assert _logged_types(eliminated, competition) == []


def test_new_gameweek_open_not_sent_when_no_following_matchweek(ctx):
    sent = ctx
    user = _make_user()
    competition = _make_competition(user, 'Final League')
    _join(user, competition)
    _subscribe(user, results_confirmed=True)

    notify_results_confirmed(competition, 5)

    assert sent == []


# 6. A player with multiple competitions is evaluated independently in each.
def test_multi_competition_player_evaluated_per_competition(ctx):
    sent = ctx
    user = _make_user()
    comp_out = _make_competition(user, 'Out Of This One')
    comp_in = _make_competition(user, 'Still In This One')
    _join(user, comp_out)
    _join(user, comp_in)
    _eliminate(user, comp_out)
    _keep_alive(user, comp_in)
    assert _lives(user, comp_out) == 0
    assert _lives(user, comp_in) == 3
    _subscribe(user, deadline_48h=True, deadline_passed=True, results_confirmed=True)

    check_and_send_deadline_notifications(now=IN_48H_WINDOW)

    assert len(sent) == 1
    assert _payload(sent[0])['url'] == f'/submit_pick/{comp_in.id}/{UPCOMING_MATCHWEEK}'
    assert _logged_types(user, comp_out) == []
    assert _logged_types(user, comp_in) == ['deadline_48h']

    # Selections viewable goes to both competitions; new week open only to the live one.
    check_and_send_deadline_notifications(now=AFTER_DEADLINE)
    notify_results_confirmed(comp_out, 3)
    notify_results_confirmed(comp_in, 3)

    assert _logged_types(user, comp_out) == ['deadline_passed']
    assert _logged_types(user, comp_in) == ['deadline_48h', 'deadline_passed', 'results_confirmed']
    assert len(sent) == 4


# 7. Selecting between the 48h and 24h reminders suppresses the 24h reminder.
def test_selecting_after_48h_reminder_suppresses_24h_reminder(ctx):
    sent = ctx
    user = _make_user()
    competition = _make_competition(user, 'Late Picker League')
    _join(user, competition)
    _subscribe(user, deadline_48h=True, deadline_24h=True)

    check_and_send_deadline_notifications(now=IN_48H_WINDOW)
    assert _logged_types(user, competition) == ['deadline_48h']

    _pick_upcoming(user, competition)
    check_and_send_deadline_notifications(now=IN_24H_WINDOW)

    assert len(sent) == 1
    assert _logged_types(user, competition) == ['deadline_48h']


def test_48h_reminder_is_not_sent_inside_the_24h_window_or_after_deadline(ctx):
    sent = ctx
    user = _make_user()
    competition = _make_competition(user, 'Window League')
    _join(user, competition)
    _subscribe(user, deadline_48h=True, deadline_24h=True)

    check_and_send_deadline_notifications(now=BEFORE_ANY_WINDOW)
    assert sent == []

    # First run is already inside the 24h window: only the 24h reminder goes out.
    check_and_send_deadline_notifications(now=IN_24H_WINDOW)
    assert _logged_types(user, competition) == ['deadline_24h']

    # After the deadline no reminder is sent, even though neither window was fully served.
    check_and_send_deadline_notifications(now=AFTER_DEADLINE)
    assert _logged_types(user, competition) == ['deadline_24h']
    assert len(sent) == 1


# 8. Lives are evaluated at dispatch using the authoritative calculation, not a cached value.
def test_lives_lost_before_dispatch_use_current_lives_not_cached_value(ctx):
    sent = ctx
    user = _make_user()
    competition = _make_competition(user, 'Stale Lives League')
    _join(user, competition)
    _subscribe(user, deadline_48h=True)

    check_and_send_deadline_notifications(now=BEFORE_ANY_WINDOW)
    assert sent == []

    # Results arrive that eliminate the player, but nothing has recalculated the member row yet.
    _pick_past_weeks(user, competition, losing=True)
    _record_past_results()
    assert _lives(user, competition) == 3

    check_and_send_deadline_notifications(now=IN_48H_WINDOW)

    assert sent == []
    assert _lives(user, competition) == 0


def test_lives_restored_before_dispatch_use_current_lives_not_cached_value(ctx):
    sent = ctx
    user = _make_user()
    competition = _make_competition(user, 'Restored Lives League')
    _join(user, competition)
    _eliminate(user, competition)
    assert _lives(user, competition) == 0
    _subscribe(user, deadline_48h=True)

    # Admin corrects the matchweek 3 result so the player's pick (Fulham) actually won.
    FixtureResult.query.filter_by(matchweek=3).update({'result': 'away_win'})
    MatchweekOutcome.query.filter_by(matchweek=3).update({'resolved': False})
    db.session.commit()
    assert _lives(user, competition) == 0  # still the stale value on the member row

    check_and_send_deadline_notifications(now=IN_48H_WINDOW)

    assert len(sent) == 1
    assert _lives(user, competition) == 1


# 9. Users never receive categories they have disabled.
def test_disabled_categories_are_never_sent(ctx):
    sent = ctx
    only_viewable = _make_user()
    nothing = _make_user()
    no_pref_row = _make_user()
    competition = _make_competition(only_viewable, 'Opt Out League')
    for user in (only_viewable, nothing, no_pref_row):
        _join(user, competition)
    _subscribe(only_viewable, deadline_passed=True)
    _subscribe(nothing)  # all four categories off
    db.session.add(PushSubscription(
        user_id=no_pref_row.id, endpoint='https://push.example.com/no-pref', p256dh='p', auth='a',
    ))
    db.session.commit()

    check_and_send_deadline_notifications(now=IN_48H_WINDOW)
    check_and_send_deadline_notifications(now=IN_24H_WINDOW)
    check_and_send_deadline_notifications(now=AFTER_DEADLINE)
    notify_results_confirmed(competition, 3)

    assert len(sent) == 1
    assert _logged_types(only_viewable, competition) == ['deadline_passed']
    assert _logged_types(nothing, competition) == []
    assert _logged_types(no_pref_row, competition) == []


def test_each_category_respects_its_own_preference(ctx):
    sent = ctx
    users = {
        name: _make_user()
        for name in ('deadline_48h', 'deadline_24h', 'deadline_passed', 'results_confirmed')
    }
    competition = _make_competition(users['deadline_48h'], 'Independent Prefs League')
    for name, user in users.items():
        _join(user, competition)
        _subscribe(user, **{name: True})

    check_and_send_deadline_notifications(now=IN_48H_WINDOW)
    check_and_send_deadline_notifications(now=IN_24H_WINDOW)
    check_and_send_deadline_notifications(now=AFTER_DEADLINE)
    notify_results_confirmed(competition, 3)

    assert len(sent) == 4
    for name, user in users.items():
        assert _logged_types(user, competition) == [name]
