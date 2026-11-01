import fcntl

import pytest

import run_notification_job


@pytest.fixture
def quiet_db(monkeypatch):
    monkeypatch.setattr(run_notification_job, "prepare_database", lambda: None)


def test_run_notification_job_main_returns_zero_when_job_succeeds(monkeypatch, quiet_db):
    called = {"value": False}

    def fake_job() -> None:
        called["value"] = True

    monkeypatch.setattr(run_notification_job, "check_and_send_deadline_notifications", fake_job)

    assert run_notification_job.main() == 0
    assert called["value"] is True


def test_run_notification_job_main_returns_one_when_job_fails(monkeypatch, quiet_db):
    def fake_job() -> None:
        raise RuntimeError("boom")

    monkeypatch.setattr(run_notification_job, "check_and_send_deadline_notifications", fake_job)

    assert run_notification_job.main() == 1


def test_run_notification_job_skips_when_another_run_holds_the_lock(monkeypatch, quiet_db, tmp_path, capsys):
    lock_path = tmp_path / "notification_job.lock"
    monkeypatch.setattr(run_notification_job, "_lock_path", lambda: lock_path)
    called = {"value": False}
    monkeypatch.setattr(
        run_notification_job, "check_and_send_deadline_notifications",
        lambda: called.__setitem__("value", True),
    )

    with lock_path.open("w") as holder:
        fcntl.flock(holder, fcntl.LOCK_EX | fcntl.LOCK_NB)
        assert run_notification_job.main() == 0

    assert called["value"] is False
    assert "already running" in capsys.readouterr().out

    # Once the lock is released the job runs normally.
    assert run_notification_job.main() == 0
    assert called["value"] is True
