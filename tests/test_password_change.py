import pytest

from app import User, app, db, ensure_default_admin_account


OLD_PASSWORD = "original password 123"
NEW_PASSWORD = "a better password 456"


@pytest.fixture
def client(monkeypatch):
    import app as app_module

    monkeypatch.setitem(app.config, "TESTING", True)
    monkeypatch.setitem(app.config, "SMTP_HOST", "")
    monkeypatch.setattr(app_module, "STARTUP_INITIALISED", False)
    with app.app_context():
        db.create_all()
        user = User(name="Password Tester", email="password@example.com", phone="07000000999")
        user.set_password(OLD_PASSWORD)
        db.session.add(user)
        db.session.commit()
    with app.test_client() as test_client:
        yield test_client
    with app.app_context():
        db.session.remove()
        db.drop_all()


def login_and_token(client):
    client.post("/login", data={"email": "password@example.com", "password": OLD_PASSWORD})
    response = client.get("/change_password")
    assert response.status_code == 200
    assert b'autocomplete="new-password"' in response.data
    assert b'Show passwords' in response.data
    with client.session_transaction() as session:
        return session["password_change_csrf"]


def assert_old_password_unchanged():
    with app.app_context():
        user = User.query.filter_by(email="password@example.com").one()
        assert user.check_password(OLD_PASSWORD)
        assert not user.check_password("reset123")


@pytest.mark.parametrize("method", ["get", "post"])
def test_password_change_requires_login(client, method):
    response = getattr(client, method)("/change_password")
    assert response.status_code == 302
    assert "/login" in response.location
    assert_old_password_unchanged()


def test_password_change_success_and_login(client):
    token = login_and_token(client)
    assert b'Change password' in client.get("/").data
    response = client.post("/change_password", data={
        "csrf_token": token,
        "current_password": OLD_PASSWORD,
        "new_password": NEW_PASSWORD,
        "confirm_password": NEW_PASSWORD,
    }, follow_redirects=True)
    assert response.status_code == 200
    assert b"Your password has been changed" in response.data
    assert b"Welcome back" not in response.data
    with client.session_transaction() as session:
        assert "_user_id" not in session
        assert "password_change_csrf" not in session
    assert client.get_cookie("remember_token") is None
    with app.app_context():
        user = User.query.filter_by(email="password@example.com").one()
        assert user.check_password(NEW_PASSWORD)
        assert not user.check_password(OLD_PASSWORD)
    failed_login = client.post("/login", data={"email": "password@example.com", "password": OLD_PASSWORD})
    assert b"Invalid email or password" in failed_login.data
    successful_login = client.post("/login", data={"email": "password@example.com", "password": NEW_PASSWORD})
    assert successful_login.status_code == 302
    assert successful_login.location.endswith("/")


@pytest.mark.parametrize("current,new,confirmation,message", [
    ("wrong", NEW_PASSWORD, NEW_PASSWORD, b"current password is incorrect"),
    (OLD_PASSWORD, "short", "short", b"between 12 and 128"),
    (OLD_PASSWORD, "x" * 129, "x" * 129, b"between 12 and 128"),
    (OLD_PASSWORD, NEW_PASSWORD, "different", b"do not match"),
    (OLD_PASSWORD, OLD_PASSWORD, OLD_PASSWORD, b"different from your current"),
])
def test_password_change_validation(client, current, new, confirmation, message):
    token = login_and_token(client)
    response = client.post("/change_password", data={
        "csrf_token": token,
        "current_password": current,
        "new_password": new,
        "confirm_password": confirmation,
    })
    assert response.status_code == 200
    assert message in response.data
    assert_old_password_unchanged()
    # Passwords must never be reflected into the form after a failed submission.
    assert f'value="{new}"'.encode() not in response.data


@pytest.mark.parametrize("token", ["", "incorrect", "é"])
def test_password_change_rejects_invalid_csrf(client, token):
    original_token = login_and_token(client)
    response = client.post("/change_password", data={
        "csrf_token": token,
        "current_password": OLD_PASSWORD,
        "new_password": NEW_PASSWORD,
        "confirm_password": NEW_PASSWORD,
    })
    assert response.status_code == 400
    assert b"form has expired" in response.data
    assert_old_password_unchanged()
    with client.session_transaction() as session:
        assert session["password_change_csrf"] != original_token


@pytest.mark.parametrize("identifier", ["password@example.com", "07000000999", "unknown@example.com"])
def test_unverified_recovery_never_changes_password(client, identifier):
    response = client.post("/forgot_password", data={"identifier": identifier})
    assert response.status_code == 200
    assert b"Email recovery is not configured yet" in response.data
    assert b"reset123" not in response.data
    assert b'<form' not in response.data
    assert_old_password_unchanged()


def test_logged_in_recovery_redirects_to_password_change(client):
    login_and_token(client)
    response = client.get("/forgot_password")
    assert response.status_code == 302
    assert response.location.endswith("/change_password")


def test_admin_initialisation_preserves_changed_password(client):
    with app.app_context():
        ensure_default_admin_account()
        admin = User.query.filter_by(name="Jamie C").one()
        admin.set_password(NEW_PASSWORD)
        db.session.commit()
        ensure_default_admin_account()
        db.session.refresh(admin)
        assert admin.check_password(NEW_PASSWORD)
        assert admin.is_admin