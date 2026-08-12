import run_notification_job


def test_run_notification_job_main_returns_zero_when_job_succeeds(monkeypatch):
    called = {"value": False}

    def fake_job() -> None:
        called["value"] = True

    monkeypatch.setattr(run_notification_job, "check_and_send_deadline_notifications", fake_job)

    assert run_notification_job.main() == 0
    assert called["value"] is True


def test_run_notification_job_main_returns_one_when_job_fails(monkeypatch):
    def fake_job() -> None:
        raise RuntimeError("boom")

    monkeypatch.setattr(run_notification_job, "check_and_send_deadline_notifications", fake_job)

    assert run_notification_job.main() == 1
