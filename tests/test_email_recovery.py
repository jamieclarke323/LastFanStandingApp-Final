import hashlib
import re
import smtplib
from datetime import timedelta
from urllib.parse import parse_qs, urlsplit

import pytest

import app as app_module
import recovery_email
from app import PasswordResetToken, User, app, db


OLD = "original password 123"
NEW = "new recovery passphrase 456"


@pytest.fixture
def recovery(monkeypatch):
    config = {
        "TESTING": True,
        "SECRET_KEY": "test-secret-only-not-for-production-123456",
        "PUBLIC_BASE_URL": "https://lfs.example.com",
        "SMTP_HOST": "smtp.example.com",
        "SMTP_PORT": 587,
        "SMTP_USERNAME": "test-user",
        "SMTP_PASSWORD": "fake-test-credential",
        "SMTP_FROM": "LFS <no-reply@example.com>",
        "SMTP_SSL": False,
    }
    for key, value in config.items():
        monkeypatch.setitem(app.config, key, value)
    monkeypatch.setattr(app_module, "STARTUP_INITIALISED", False)
    messages = []
    monkeypatch.setattr(app_module, "send_password_reset_email", lambda config, recipient, url: messages.append((recipient, url)))
    with app.app_context():
        db.create_all()
        user = User(name="Recovery Tester", email="MixedCase@example.com", phone="07000000555")
        user.set_password(OLD)
        db.session.add(user)
        db.session.commit()
    with app.test_client() as client:
        yield client, messages
    with app.app_context():
        db.session.remove()
        db.drop_all()


def request_link(client, email=" mixedcase@EXAMPLE.COM ", **kwargs):
    client.get("/forgot_password")
    with client.session_transaction() as session:
        csrf = session["recovery_csrf"]
    return client.post("/forgot_password", data={"email": email, "csrf_token": csrf}, **kwargs)


def open_link(client, url):
    parsed = urlsplit(url)
    return client.get(parsed.path + "?" + parsed.query, follow_redirects=True)


def reset_form(client, password=NEW, confirmation=NEW):
    with client.session_transaction() as session:
        csrf = session["recovery_csrf"]
    return {"csrf_token": csrf, "new_password": password, "confirm_password": confirmation}


def unchanged():
    with app.app_context():
        assert User.query.filter_by(phone="07000000555").one().check_password(OLD)


def test_email_recovery_complete_and_single_use(recovery):
    client, messages = recovery
    response = request_link(client, follow_redirects=True)
    assert b"If an account exists" in response.data
    assert len(messages) == 1
    recipient, url = messages[0]
    assert recipient == "MixedCase@example.com"
    assert url.startswith("https://lfs.example.com/reset_password?token=")
    raw_token = parse_qs(urlsplit(url).query)["token"][0]
    assert raw_token.encode() not in response.data
    with app.app_context():
        reset = PasswordResetToken.query.one()
        assert reset.token_hash == hashlib.sha256(raw_token.encode()).hexdigest()
        assert reset.used_at is None
        assert 29 * 60 < (reset.expires_at - app_module.recovery_now()).total_seconds() <= 30 * 60
    unchanged()
    form = open_link(client, url)
    assert form.status_code == 200
    assert form.request.path == "/reset_password"
    assert not form.request.query_string
    assert raw_token.encode() not in form.data
    assert form.headers["Cache-Control"] == "no-store"
    assert form.headers["Referrer-Policy"] == "no-referrer"
    unchanged()  # A mail scanner opening a link must not consume it.
    success = client.post("/reset_password", data=reset_form(client), follow_redirects=True)
    assert b"Your password has been reset" in success.data
    with app.app_context():
        user = User.query.filter_by(phone="07000000555").one()
        assert user.check_password(NEW)
        assert not user.check_password(OLD)
        assert PasswordResetToken.query.one().used_at is not None
    assert open_link(client, url).status_code == 400
    assert client.post("/login", data={"email": "MIXEDCASE@example.com", "password": NEW}).status_code == 302


def test_unknown_and_known_addresses_have_same_confirmation(recovery):
    client, messages = recovery
    known = request_link(client, follow_redirects=True)
    unknown = request_link(client, "missing@example.com", follow_redirects=True)
    assert known.status_code == unknown.status_code == 200
    assert known.data == unknown.data
    assert len(messages) == 1
    unchanged()


@pytest.mark.parametrize("token", ["", "bad", "é"])
def test_request_requires_csrf(recovery, token):
    client, messages = recovery
    response = client.post("/forgot_password", data={"email": "mixedcase@example.com", "csrf_token": token})
    assert response.status_code == 400
    assert messages == []
    unchanged()


@pytest.mark.parametrize("email", ["", "invalid", "x" * 121 + "@example.com"])
def test_invalid_email_input(recovery, email):
    client, messages = recovery
    assert request_link(client, email).status_code == 400
    assert messages == []
    unchanged()


def test_invalid_expired_and_changed_password_links(recovery):
    client, messages = recovery
    assert open_link(client, "/reset_password?token=" + "a" * 43).status_code == 400
    request_link(client)
    with app.app_context():
        PasswordResetToken.query.one().expires_at = app_module.recovery_now() - timedelta(seconds=1)
        db.session.commit()
    assert open_link(client, messages[-1][1]).status_code == 400
    request_link(client)
    with app.app_context():
        user = User.query.filter_by(phone="07000000555").one()
        user.set_password("another changed password")
        db.session.commit()
    assert open_link(client, messages[-1][1]).status_code == 400


@pytest.mark.parametrize("password,confirmation,message", [
    ("short", "short", b"between 12 and 128"),
    ("x" * 129, "x" * 129, b"between 12 and 128"),
    (NEW, "mismatch", b"do not match"),
    (OLD, OLD, b"different from your current"),
])
def test_reset_validation_preserves_link(recovery, password, confirmation, message):
    client, messages = recovery
    request_link(client)
    open_link(client, messages[0][1])
    response = client.post("/reset_password", data=reset_form(client, password, confirmation))
    assert response.status_code == 200
    assert message in response.data
    assert f'value="{password}"'.encode() not in response.data
    unchanged()
    assert client.post("/reset_password", data=reset_form(client)).status_code == 302


def test_reset_requires_csrf(recovery):
    client, messages = recovery
    request_link(client)
    open_link(client, messages[0][1])
    data = reset_form(client)
    data["csrf_token"] = "invalid"
    assert client.post("/reset_password", data=data).status_code == 400
    unchanged()


def test_link_expiring_after_form_open_cannot_reset(recovery):
    client, messages = recovery
    request_link(client)
    open_link(client, messages[0][1])
    data = reset_form(client)
    with app.app_context():
        PasswordResetToken.query.one().expires_at = app_module.recovery_now() - timedelta(seconds=1)
        db.session.commit()
    assert client.post("/reset_password", data=data).status_code == 400
    unchanged()


def test_reset_submission_rate_limit(recovery):
    client, messages = recovery
    request_link(client)
    open_link(client, messages[0][1])
    data = reset_form(client, "short", "short")
    for _ in range(20):
        assert client.post("/reset_password", data=data).status_code == 200
    assert client.post("/reset_password", data=reset_form(client)).status_code == 429
    unchanged()


def test_recovery_rate_limits(recovery):
    client, messages = recovery
    for _ in range(4):
        assert request_link(client).status_code == 302
    assert len(messages) == 3
    for i in range(6):
        assert request_link(client, f"unknown{i}@example.com").status_code == 302
    assert request_link(client).status_code == 429
    unchanged()


def test_smtp_failure_is_generic_and_does_not_log_link(recovery, monkeypatch, caplog):
    client, messages = recovery
    def fail(config, recipient, url):
        messages.append((recipient, url))
        raise smtplib.SMTPException("secret test failure " + url)
    monkeypatch.setattr(app_module, "send_password_reset_email", fail)
    response = request_link(client, follow_redirects=True)
    assert response.status_code == 200
    assert b"If an account exists" in response.data
    assert "Password recovery delivery failed" in caplog.text
    assert "secret test failure" not in caplog.text
    assert messages[0][1] not in caplog.text
    assert open_link(client, messages[0][1]).status_code == 400
    unchanged()


def test_reset_invalidates_other_links_sessions_and_remember_cookies(recovery):
    client, messages = recovery
    other = app.test_client()
    other.post("/login", data={"email": "mixedcase@example.com", "password": OLD})
    remembered = other.get_cookie("remember_token").value
    assert other.get("/change_password").status_code == 200
    request_link(client)
    request_link(client)
    open_link(client, messages[0][1])
    assert client.post("/reset_password", data=reset_form(client)).status_code == 302
    assert open_link(client, messages[1][1]).status_code == 400
    assert other.get("/change_password").status_code == 302
    cookie_only = app.test_client()
    cookie_only.set_cookie("remember_token", remembered)
    assert cookie_only.get("/change_password").status_code == 302


def test_two_browsers_cannot_reuse_token(recovery):
    client, messages = recovery
    request_link(client)
    other = app.test_client()
    assert open_link(client, messages[0][1]).status_code == 200
    assert open_link(other, messages[0][1]).status_code == 200
    first_data = reset_form(client)
    second_data = reset_form(other, "second attacker password", "second attacker password")
    assert client.post("/reset_password", data=first_data).status_code == 302
    assert other.post("/reset_password", data=second_data).status_code == 400


def test_link_uses_configured_origin_not_host_header(recovery):
    client, messages = recovery
    # Establish the CSRF cookie on the spoofed host too; localhost cookies must
    # not be sent to another host by the test client.
    form = client.get("/forgot_password", base_url="https://attacker.example")
    csrf = re.search(rb'name="csrf_token" value="([^"]+)"', form.data).group(1).decode()
    response = client.post("/forgot_password", base_url="https://attacker.example", data={
        "email": "mixedcase@example.com", "csrf_token": csrf,
    })
    assert response.status_code == 302
    assert messages[0][1].startswith("https://lfs.example.com/")


def test_ambiguous_case_duplicate_accounts_do_not_get_links(recovery):
    client, messages = recovery
    with app.app_context():
        duplicate = User(name="Duplicate", email="mixedcase@example.com", phone="07000000666")
        duplicate.set_password(OLD)
        db.session.add(duplicate)
        db.session.commit()
    assert request_link(client).status_code == 302
    assert not messages


@pytest.mark.parametrize("key,value", [
    ("SMTP_HOST", ""), ("SMTP_PASSWORD", ""), ("SMTP_FROM", ""),
    ("SECRET_KEY", "dev-secret-key"), ("PUBLIC_BASE_URL", "http://unsafe.example.com"),
    ("PUBLIC_BASE_URL", "https://trusted.example.com?bad=1"),
])
def test_incomplete_or_unsafe_config_disables_recovery(recovery, monkeypatch, key, value):
    client, messages = recovery
    monkeypatch.setitem(app.config, key, value)
    response = client.get("/forgot_password")
    assert b"Email recovery is not configured yet" in response.data
    assert b"Send reset link" not in response.data
    assert not messages


@pytest.mark.parametrize("implicit_tls", [False, True])
def test_smtp_delivery_uses_tls(recovery, monkeypatch, implicit_tls):
    events = []
    class FakeSMTP:
        def __init__(self, host, port, **kwargs):
            events.append((host, port, kwargs))
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def ehlo(self):
            events.append("ehlo")
        def starttls(self, context):
            assert context.check_hostname
            events.append("tls")
        def login(self, username, password):
            events.append("login")
        def send_message(self, message):
            assert message["To"] == "recipient@example.com"
            assert "expires in 30 minutes" in message.get_content()
            assert "https://lfs.example.com/reset_password?token=test" in message.get_content()
            events.append("sent")
            return {}
    monkeypatch.setattr(recovery_email.smtplib, "SMTP_SSL" if implicit_tls else "SMTP", FakeSMTP)
    monkeypatch.setitem(app.config, "SMTP_SSL", implicit_tls)
    recovery_email.send_password_reset_email(app.config, "recipient@example.com", "https://lfs.example.com/reset_password?token=test")
    assert events[-2:] == ["login", "sent"]
    if implicit_tls:
        assert events[0][2]["context"].check_hostname
    else:
        assert events.index("tls") < events.index("login")