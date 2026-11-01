"""Delivery reliability, idempotency and preference-activation behaviour for push
notifications: per-device records, transient retries, invalid-subscription cleanup,
deadline changes, the selections-viewable grace window, first-activation defaults,
unsubscribe, the schema upgrade and the admin test tool."""
import json
from datetime import datetime, timedelta
from itertools import count
from types import SimpleNamespace

import pytest
from pywebpush import WebPushException

import app as app_module
import send_test_notification
from app import (
    Competition,
    CompetitionMember,
    NotificationDelivery,
    NotificationLog,
    NotificationPreference,
    PushSubscription,
    User,
    app,
    check_and_send_deadline_notifications,
    db,
    ensure_schema_upgrades,
)

# Single upcoming matchweek: kickoff 10/01/2030 12:00 UK (== UTC in January), so the
# deadline is 10/01/2030 11:00 UTC.
FIXTURES = [
    {'matchweek': 1, 'fixtures': [{'home_team': 'Spurs', 'away_team': 'Leeds', 'location': 'X', 'date': '10/01/2030 12:00'}]},
    {'matchweek': 2, 'fixtures': [{'home_team': 'Man City', 'away_team': 'Sunderland', 'location': 'X', 'date': '17/01/2030 12:00'}]},
]
DEADLINE = datetime(2030, 1, 10, 11, 0)
IN_48H_WINDOW = DEADLINE - timedelta(hours=47)
IN_24H_WINDOW = DEADLINE - timedelta(hours=23)
LONG_AGO = datetime(2020, 1, 1)

_sequence = count(1)


@pytest.fixture
def ctx(monkeypatch):
    app.config.update(TESTING=True, SQLALCHEMY_DATABASE_URI='sqlite:///:memory:')
    monkeypatch.setattr(app_module, 'NOTIFICATIONS_ENABLED', True)
    monkeypatch.setattr(app_module, 'FIXTURES_BY_MATCHWEEK', FIXTURES)
    with app.app_context():
        db.create_all()
        yield
        db.session.remove()
        db.drop_all()


@pytest.fixture
def sent(monkeypatch):
    calls = []
    monkeypatch.setattr(app_module, 'webpush', lambda **kwargs: calls.append(kwargs))
    return calls


def _make_user():
    n = next(_sequence)
    user = User(name=f'Player {n}', email=f'delivery{n}@example.com', phone=f'0710000{n:04d}')
    user.set_password('password123')
    db.session.add(user)
    db.session.commit()
    return user


def _make_competition(admin, name='Delivery League'):
    competition = Competition(name=name, code=f'DLV{next(_sequence)}', admin_id=admin.id, start_matchweek=1)
    db.session.add(competition)
    db.session.commit()
    db.session.add(CompetitionMember(competition_id=competition.id, user_id=admin.id, lives=3, active=True))
    db.session.commit()
    return competition


def _subscribe(user, created_at=LONG_AGO):
    subscription = PushSubscription(
        user_id=user.id,
        endpoint=f'https://push.example.com/{user.id}-{next(_sequence)}',
        p256dh='p256dh',
        auth='auth',
        created_at=created_at,
    )
    db.session.add(subscription)
    db.session.commit()
    return subscription


def _prefs(user, **flags):
    db.session.add(NotificationPreference(user_id=user.id, **flags))
    db.session.commit()


def _deliveries():
    return NotificationDelivery.query.order_by(NotificationDelivery.id).all()


def _endpoints(calls):
    return [call['subscription_info']['endpoint'] for call in calls]


def _login(client, user):
    client.post('/login', data={'email': user.email, 'password': 'password123'})


def _push_error(status_code):
    return WebPushException('push failed', response=SimpleNamespace(status_code=status_code))


# --- per-device delivery ------------------------------------------------------------

def test_each_device_gets_one_delivery_and_reruns_do_not_resend(ctx, sent):
    user = _make_user()
    _make_competition(user)
    phone = _subscribe(user)
    laptop = _subscribe(user)
    _prefs(user, deadline_48h=True)

    check_and_send_deadline_notifications(now=IN_48H_WINDOW)
    check_and_send_deadline_notifications(now=IN_48H_WINDOW + timedelta(hours=1))

    assert sorted(_endpoints(sent)) == sorted([phone.endpoint, laptop.endpoint])
    assert NotificationLog.query.count() == 1
    assert [(d.subscription_id, d.status, d.attempts) for d in _deliveries()] == [
        (phone.id, 'sent', 1), (laptop.id, 'sent', 1),
    ]


def test_transient_failure_is_retried_on_a_later_run_until_it_succeeds(ctx, monkeypatch):
    user = _make_user()
    _make_competition(user)
    subscription = _subscribe(user)
    _prefs(user, deadline_48h=True)

    attempts = []

    def flaky_webpush(**kwargs):
        attempts.append(kwargs)
        if len(attempts) == 1:
            raise _push_error(503)

    monkeypatch.setattr(app_module, 'webpush', flaky_webpush)

    check_and_send_deadline_notifications(now=IN_48H_WINDOW)
    delivery = _deliveries()[0]
    assert (delivery.status, delivery.attempts, delivery.last_error) == ('failed', 1, 'HTTP 503')

    check_and_send_deadline_notifications(now=IN_48H_WINDOW + timedelta(hours=1))
    delivery = _deliveries()[0]
    assert (delivery.status, delivery.attempts, delivery.last_error) == ('sent', 2, None)
    assert len(attempts) == 2
    assert PushSubscription.query.get(subscription.id) is not None


def test_transient_failures_stop_after_max_attempts(ctx, monkeypatch):
    user = _make_user()
    _make_competition(user)
    _subscribe(user)
    _prefs(user, deadline_48h=True)
    attempts = []

    def always_down(**kwargs):
        attempts.append(kwargs)
        raise ConnectionError('network down')

    monkeypatch.setattr(app_module, 'webpush', always_down)

    for hour in range(app_module.MAX_DELIVERY_ATTEMPTS + 2):
        check_and_send_deadline_notifications(now=IN_48H_WINDOW + timedelta(hours=hour))

    assert len(attempts) == app_module.MAX_DELIVERY_ATTEMPTS
    delivery = _deliveries()[0]
    assert delivery.status == 'failed'
    assert delivery.last_error == 'network down'


def test_invalid_subscription_is_removed_and_other_devices_still_served(ctx, monkeypatch):
    user = _make_user()
    _make_competition(user)
    dead = _subscribe(user)
    live = _subscribe(user)
    _prefs(user, deadline_48h=True)
    calls = []

    def webpush_with_gone(**kwargs):
        calls.append(kwargs)
        if kwargs['subscription_info']['endpoint'] == dead.endpoint:
            raise _push_error(410)

    monkeypatch.setattr(app_module, 'webpush', webpush_with_gone)
    dead_id, live_id = dead.id, live.id

    check_and_send_deadline_notifications(now=IN_48H_WINDOW)

    assert PushSubscription.query.get(dead_id) is None
    assert PushSubscription.query.get(live_id) is not None
    assert [(d.subscription_id, d.status) for d in _deliveries()] == [(live_id, 'sent')]
    assert NotificationLog.query.count() == 1


def test_one_device_failing_does_not_block_the_next_member(ctx, monkeypatch):
    first = _make_user()
    second = _make_user()
    competition = _make_competition(first)
    db.session.add(CompetitionMember(competition_id=competition.id, user_id=second.id, lives=3, active=True))
    db.session.commit()
    broken = _subscribe(first)
    fine = _subscribe(second)
    _prefs(first, deadline_48h=True)
    _prefs(second, deadline_48h=True)

    def webpush_raising(**kwargs):
        if kwargs['subscription_info']['endpoint'] == broken.endpoint:
            raise RuntimeError('unexpected explosion')

    monkeypatch.setattr(app_module, 'webpush', webpush_raising)

    check_and_send_deadline_notifications(now=IN_48H_WINDOW)

    statuses = {d.subscription_id: d.status for d in _deliveries()}
    assert statuses == {broken.id: 'failed', fine.id: 'sent'}


def test_device_registered_after_the_event_does_not_get_old_notification(ctx, sent):
    user = _make_user()
    _make_competition(user)
    old_device = _subscribe(user)
    _prefs(user, deadline_48h=True)

    check_and_send_deadline_notifications(now=IN_48H_WINDOW)
    assert _endpoints(sent) == [old_device.endpoint]

    new_device = _subscribe(user, created_at=IN_48H_WINDOW + timedelta(minutes=30))
    check_and_send_deadline_notifications(now=IN_48H_WINDOW + timedelta(hours=1))

    assert _endpoints(sent) == [old_device.endpoint]
    assert new_device.id not in {d.subscription_id for d in _deliveries()}


def test_push_requests_carry_a_ttl_and_timeout(ctx, sent):
    user = _make_user()
    _make_competition(user)
    _subscribe(user)
    _prefs(user, deadline_48h=True, deadline_passed=True)

    check_and_send_deadline_notifications(now=IN_48H_WINDOW)
    check_and_send_deadline_notifications(now=DEADLINE + timedelta(hours=1))

    reminder, revealed = sent
    assert reminder['ttl'] == 47 * 3600  # expires at the deadline
    assert revealed['ttl'] == app_module.DEFAULT_PUSH_TTL_SECONDS
    assert all(call['timeout'] == app_module.PUSH_REQUEST_TIMEOUT_SECONDS for call in sent)


def test_rows_committed_before_sending_are_retried_if_the_run_crashes_mid_send(ctx, monkeypatch):
    user = _make_user()
    _make_competition(user)
    _subscribe(user)
    _prefs(user, deadline_48h=True)
    attempts = []

    def crash(**kwargs):
        attempts.append(kwargs)
        raise KeyboardInterrupt  # not caught by deliver_push: simulates the process dying

    monkeypatch.setattr(app_module, 'webpush', crash)
    with pytest.raises(KeyboardInterrupt):
        check_and_send_deadline_notifications(now=IN_48H_WINDOW)
    db.session.rollback()

    delivery = _deliveries()[0]
    assert (delivery.status, delivery.attempts) == ('pending', 0)

    monkeypatch.setattr(app_module, 'webpush', lambda **kwargs: attempts.append(kwargs))
    check_and_send_deadline_notifications(now=IN_48H_WINDOW + timedelta(hours=1))
    assert _deliveries()[0].status == 'sent'
    assert len(attempts) == 2


def test_user_without_devices_is_logged_so_a_later_device_is_not_flooded(ctx, sent):
    user = _make_user()
    _make_competition(user)
    _prefs(user, deadline_48h=True)

    check_and_send_deadline_notifications(now=IN_48H_WINDOW)
    assert sent == []
    assert NotificationLog.query.count() == 1

    _subscribe(user, created_at=IN_48H_WINDOW + timedelta(minutes=5))
    check_and_send_deadline_notifications(now=IN_48H_WINDOW + timedelta(hours=2))
    assert sent == []


# --- deadline changes & windows -----------------------------------------------------

def test_reminder_is_raised_again_when_the_deadline_moves(ctx, sent, monkeypatch):
    user = _make_user()
    _make_competition(user)
    _subscribe(user)
    _prefs(user, deadline_48h=True)

    check_and_send_deadline_notifications(now=IN_48H_WINDOW)
    assert len(sent) == 1
    assert NotificationLog.query.one().deadline_at == DEADLINE

    # Admin moves the fixture back a week: new deadline 17/01/2030 11:00.
    moved = [
        {'matchweek': 1, 'fixtures': [{'home_team': 'Spurs', 'away_team': 'Leeds', 'location': 'X', 'date': '17/01/2030 12:00'}]},
        {'matchweek': 2, 'fixtures': [{'home_team': 'Man City', 'away_team': 'Sunderland', 'location': 'X', 'date': '24/01/2030 12:00'}]},
    ]
    monkeypatch.setattr(app_module, 'FIXTURES_BY_MATCHWEEK', moved)
    new_deadline = DEADLINE + timedelta(days=7)

    # Not in the new window yet: nothing happens, old log untouched.
    check_and_send_deadline_notifications(now=IN_48H_WINDOW + timedelta(hours=1))
    assert len(sent) == 1

    check_and_send_deadline_notifications(now=new_deadline - timedelta(hours=47))
    assert len(sent) == 2
    log = NotificationLog.query.one()
    assert log.deadline_at == new_deadline
    assert [d.status for d in _deliveries()] == ['sent']

    # And still only once for the new deadline.
    check_and_send_deadline_notifications(now=new_deadline - timedelta(hours=46))
    assert len(sent) == 2


def test_legacy_log_rows_without_deadline_are_treated_as_already_sent(ctx, sent):
    user = _make_user()
    competition = _make_competition(user)
    _subscribe(user)
    _prefs(user, deadline_48h=True)
    db.session.add(NotificationLog(
        user_id=user.id, competition_id=competition.id, matchweek=1,
        notification_type='deadline_48h', sent_at=LONG_AGO, deadline_at=None,
    ))
    db.session.commit()

    check_and_send_deadline_notifications(now=IN_48H_WINDOW)

    assert sent == []


def test_selections_viewable_sent_within_grace_window_but_not_after(ctx, sent):
    user = _make_user()
    _make_competition(user)
    _subscribe(user)
    _prefs(user, deadline_passed=True)

    # Scheduler ran late but inside the grace window - still delivered.
    check_and_send_deadline_notifications(now=DEADLINE + timedelta(hours=5))
    assert len(sent) == 1
    assert json.loads(sent[0]['data'])['url'].startswith('/view_selections/')


def test_selections_viewable_not_sent_long_after_deadline(ctx, sent):
    user = _make_user()
    _make_competition(user)
    _subscribe(user)
    _prefs(user, deadline_passed=True)

    check_and_send_deadline_notifications(now=DEADLINE + app_module.SELECTIONS_VIEWABLE_GRACE + timedelta(minutes=1))

    assert sent == []
    assert NotificationLog.query.count() == 0


def test_reminders_cover_a_matchweek_whose_window_opens_before_the_previous_week_kicks_off(ctx, sent, monkeypatch):
    # MW1 kicks off 10/01 12:00; MW2 kicks off 11/01 20:00 (deadline 11/01 18:00), so the
    # MW2 48h window opens on 09/01 18:00 while MW1 is still the "upcoming" matchweek.
    tight = [
        {'matchweek': 1, 'fixtures': [{'home_team': 'Spurs', 'away_team': 'Leeds', 'location': 'X', 'date': '10/01/2030 12:00'}]},
        {'matchweek': 2, 'fixtures': [{'home_team': 'Man City', 'away_team': 'Sunderland', 'location': 'X', 'date': '11/01/2030 20:00'}]},
    ]
    monkeypatch.setattr(app_module, 'FIXTURES_BY_MATCHWEEK', tight)
    user = _make_user()
    competition = _make_competition(user)
    _subscribe(user)
    _prefs(user, deadline_48h=True, deadline_24h=True)

    check_and_send_deadline_notifications(now=datetime(2030, 1, 9, 19, 0))

    logged = sorted((log.matchweek, log.notification_type) for log in NotificationLog.query.all())
    assert logged == [(1, 'deadline_24h'), (2, 'deadline_48h')]
    urls = sorted(json.loads(call['data'])['url'] for call in sent)
    assert urls == [f'/submit_pick/{competition.id}/1', f'/submit_pick/{competition.id}/2']


def test_competition_starting_later_ignores_earlier_matchweeks(ctx, sent):
    user = _make_user()
    competition = _make_competition(user)
    competition.start_matchweek = 2
    db.session.commit()
    _subscribe(user)
    _prefs(user, deadline_48h=True, deadline_24h=True, deadline_passed=True)

    check_and_send_deadline_notifications(now=IN_48H_WINDOW)
    check_and_send_deadline_notifications(now=DEADLINE + timedelta(hours=1))

    assert sent == []


# --- preferences & subscription endpoints -------------------------------------------

def test_first_activation_enables_all_four_categories(ctx):
    user = _make_user()
    client = app.test_client()
    _login(client, user)

    response = client.post('/notifications/subscribe', json={
        'endpoint': 'https://push.example.com/first', 'keys': {'p256dh': 'p', 'auth': 'a'},
    })

    assert response.status_code == 200
    body = response.get_json()
    assert body['first_activation'] is True
    assert body['preferences'] == {
        'deadline_48h': True, 'deadline_24h': True, 'deadline_passed': True, 'results_confirmed': True,
    }
    preference = NotificationPreference.query.filter_by(user_id=user.id).one()
    assert preference.activated_at is not None


def test_later_devices_do_not_reset_saved_preferences(ctx):
    user = _make_user()
    client = app.test_client()
    _login(client, user)
    client.post('/notifications/subscribe', json={
        'endpoint': 'https://push.example.com/phone', 'keys': {'p256dh': 'p', 'auth': 'a'},
    })
    client.post('/notifications', data={'deadline_24h': 'on'})

    response = client.post('/notifications/subscribe', json={
        'endpoint': 'https://push.example.com/laptop', 'keys': {'p256dh': 'p', 'auth': 'a'},
    })

    assert response.get_json()['first_activation'] is False
    assert response.get_json()['preferences'] == {
        'deadline_48h': False, 'deadline_24h': True, 'deadline_passed': False, 'results_confirmed': False,
    }
    assert PushSubscription.query.filter_by(user_id=user.id).count() == 2


def test_preferences_saved_before_enabling_are_respected(ctx):
    user = _make_user()
    client = app.test_client()
    _login(client, user)
    client.post('/notifications', data={'results_confirmed': 'on'})

    response = client.post('/notifications/subscribe', json={
        'endpoint': 'https://push.example.com/phone', 'keys': {'p256dh': 'p', 'auth': 'a'},
    })

    assert response.get_json()['first_activation'] is False
    preference = NotificationPreference.query.filter_by(user_id=user.id).one()
    assert (preference.deadline_48h, preference.results_confirmed) == (False, True)


def test_all_four_can_be_disabled_and_page_does_not_reset_them(ctx):
    user = _make_user()
    client = app.test_client()
    _login(client, user)
    client.post('/notifications/subscribe', json={
        'endpoint': 'https://push.example.com/phone', 'keys': {'p256dh': 'p', 'auth': 'a'},
    })

    client.post('/notifications', data={})
    client.get('/notifications')

    preference = NotificationPreference.query.filter_by(user_id=user.id).one()
    assert not any(getattr(preference, name) for name in app_module.NOTIFICATION_TYPES)


def test_unsubscribe_removes_only_the_callers_own_device(ctx):
    owner = _make_user()
    other = _make_user()
    _subscribe(owner)
    mine = _subscribe(owner)
    theirs = _subscribe(other)
    client = app.test_client()
    _login(client, owner)

    assert client.post('/notifications/unsubscribe', json={'endpoint': theirs.endpoint}).status_code == 200
    assert PushSubscription.query.get(theirs.id) is not None

    assert client.post('/notifications/unsubscribe', json={'endpoint': mine.endpoint}).status_code == 200
    assert PushSubscription.query.get(mine.id) is None
    assert PushSubscription.query.filter_by(user_id=owner.id).count() == 1

    assert client.post('/notifications/unsubscribe', json={}).status_code == 400


def test_status_endpoint_reports_whether_device_is_linked(ctx):
    user = _make_user()
    subscription = _subscribe(user)
    client = app.test_client()
    _login(client, user)

    assert client.post('/notifications/status', json={'endpoint': subscription.endpoint}).get_json() == {'subscribed': True}
    assert client.post('/notifications/status', json={'endpoint': 'https://nope'}).get_json() == {'subscribed': False}


def test_notifications_page_shows_enable_and_update_controls(ctx):
    user = _make_user()
    client = app.test_client()
    _login(client, user)

    response = client.get('/notifications')

    assert b'Enable Notifications' in response.data
    assert b'Update Notifications' in response.data
    assert b'not quite working yet' not in response.data


# --- schema upgrade -----------------------------------------------------------------

def test_schema_upgrade_adds_columns_to_legacy_tables_and_keeps_existing_choices(ctx):
    db.session.execute(db.text('DROP TABLE notification_delivery'))
    db.session.execute(db.text('DROP TABLE notification_log'))
    db.session.execute(db.text('DROP TABLE notification_preference'))
    db.session.execute(db.text(
        'CREATE TABLE notification_preference (id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL UNIQUE, '
        'deadline_48h BOOLEAN, deadline_24h BOOLEAN, deadline_passed BOOLEAN, results_confirmed BOOLEAN)'
    ))
    db.session.execute(db.text(
        'CREATE TABLE notification_log (id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL, competition_id INTEGER NOT NULL, '
        'matchweek INTEGER NOT NULL, notification_type VARCHAR(30) NOT NULL, sent_at DATETIME)'
    ))
    db.session.execute(db.text(
        'INSERT INTO notification_preference (user_id, deadline_48h, deadline_24h, deadline_passed, results_confirmed) '
        'VALUES (1, 1, 0, 0, 0)'
    ))
    db.session.commit()

    app_module.prepare_database()
    app_module.prepare_database()  # idempotent

    preference = NotificationPreference.query.filter_by(user_id=1).one()
    assert (preference.deadline_48h, preference.deadline_24h) == (True, False)
    assert preference.activated_at is not None  # legacy rows never get the all-on default
    columns = {row[1] for row in db.session.execute(db.text('PRAGMA table_info(notification_log)')).fetchall()}
    assert 'deadline_at' in columns
    assert NotificationDelivery.query.count() == 0


# --- admin test tool ----------------------------------------------------------------

def test_send_test_notification_tool_sends_only_to_named_user(ctx, sent, capsys, monkeypatch):
    monkeypatch.setattr(send_test_notification, 'prepare_database', lambda: None)
    target = _make_user()
    bystander = _make_user()
    competition = _make_competition(target)
    _subscribe(target)
    _subscribe(bystander)

    assert send_test_notification.main(['--email', target.email]) == 0
    assert len(sent) == 1
    payload = json.loads(sent[0]['data'])
    assert payload['title'] == 'Last Fan Standing Test ⚽'
    assert payload['url'] == '/'

    assert send_test_notification.main([
        '--email', target.email, '--type', 'deadline_24h', '--competition-id', str(competition.id), '--matchweek', '1',
    ]) == 0
    payload = json.loads(sent[1]['data'])
    assert payload['url'] == f'/submit_pick/{competition.id}/1'
    assert competition.name in payload['body']

    # Test sends are never recorded as real deliveries.
    assert NotificationLog.query.count() == 0

    assert send_test_notification.main(['--email', 'nobody@example.com']) == 1
    assert send_test_notification.main(['--email', target.email, '--type', 'deadline_48h']) == 2
    assert send_test_notification.main(['--email', target.email, '--list']) == 0
    assert len(sent) == 2
    assert 'registered device' in capsys.readouterr().out
