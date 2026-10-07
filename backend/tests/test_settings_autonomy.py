from __future__ import annotations

from fastapi.testclient import TestClient

from app.db import init_db, reset_engine
from app.main import app


def _bootstrap_client(db_url: str) -> TestClient:
    reset_engine(db_url)
    init_db()
    client = TestClient(app)
    client.post("/api/setup/init")
    return client


def test_low_risk_daemon_time_applies_immediately(tmp_path):
    db_file = tmp_path / "settings_low_risk.db"
    with _bootstrap_client(f"sqlite:///{db_file}") as client:
        update = client.post(
            "/api/settings/update",
            json={"key": "daemon.daily_time", "new_value": "07:30", "reason": "morning run"},
        )
        assert update.status_code == 200, update.text
        payload = update.json()
        assert payload["applied"] is True
        assert payload["risk_level"] == "low"
        assert payload["proposal_id"] is None

        show = client.get("/api/settings/show/daemon.daily_time")
        assert show.status_code == 200
        assert show.json()["value"] == "07:30"


def test_weekly_day_and_time_persist_across_restart(tmp_path):
    db_file = tmp_path / "settings_restart.db"
    db_url = f"sqlite:///{db_file}"

    with _bootstrap_client(db_url) as first_client:
        first = first_client.post(
            "/api/settings/update",
            json={"key": "daemon.weekly_day", "new_value": "friday", "reason": "shift to friday"},
        )
        assert first.status_code == 200, first.text
        second = first_client.post(
            "/api/settings/update",
            json={"key": "daemon.weekly_time", "new_value": "09:15", "reason": "shift time"},
        )
        assert second.status_code == 200, second.text

    with _bootstrap_client(db_url) as second_client:
        weekly_day = second_client.get("/api/settings/show/daemon.weekly_day")
        weekly_time = second_client.get("/api/settings/show/daemon.weekly_time")
        assert weekly_day.status_code == 200
        assert weekly_time.status_code == 200
        assert weekly_day.json()["value"] == "friday"
        assert weekly_time.json()["value"] == "09:15"


def test_invalid_setting_value_rejected(tmp_path):
    db_file = tmp_path / "settings_invalid.db"
    with _bootstrap_client(f"sqlite:///{db_file}") as client:
        update = client.post(
            "/api/settings/update",
            json={"key": "daemon.daily_time", "new_value": "25:99", "reason": "bad format"},
        )
        assert update.status_code == 400
        assert "HH:MM" in update.text


def test_critical_setting_creates_pending_proposal_then_confirm(tmp_path):
    db_file = tmp_path / "settings_critical.db"
    with _bootstrap_client(f"sqlite:///{db_file}") as client:
        update = client.post(
            "/api/settings/update",
            json={"key": "model.temperature", "new_value": 0.55, "reason": "model tuning"},
        )
        assert update.status_code == 200, update.text
        payload = update.json()
        assert payload["applied"] is False
        assert payload["risk_level"] == "critical"
        proposal_id = payload["proposal_id"]
        assert proposal_id

        # Value remains unchanged until proposal confirmation.
        before_confirm = client.get("/api/settings/show/model.temperature")
        assert before_confirm.status_code == 200
        assert float(before_confirm.json()["value"]) == 0.2

        confirmed = client.post(f"/api/settings/proposals/{proposal_id}/confirm")
        assert confirmed.status_code == 200, confirmed.text
        assert confirmed.json()["applied"] is True

        after_confirm = client.get("/api/settings/show/model.temperature")
        assert after_confirm.status_code == 200
        assert float(after_confirm.json()["value"]) == 0.55


def test_settings_history_and_rollback(tmp_path):
    db_file = tmp_path / "settings_history.db"
    with _bootstrap_client(f"sqlite:///{db_file}") as client:
        set_a = client.post(
            "/api/settings/update",
            json={"key": "daemon.daily_time", "new_value": "07:30", "reason": "first shift"},
        )
        set_b = client.post(
            "/api/settings/update",
            json={"key": "daemon.daily_time", "new_value": "06:45", "reason": "second shift"},
        )
        assert set_a.status_code == 200
        assert set_b.status_code == 200

        history = client.get("/api/settings/history", params={"limit": 5, "key": "daemon.daily_time"})
        assert history.status_code == 200, history.text
        assert len(history.json()) >= 2

        rollback = client.post(
            "/api/settings/rollback",
            json={"key": "daemon.daily_time", "reason": "undo latest change"},
        )
        assert rollback.status_code == 200, rollback.text
        assert rollback.json()["applied"] is True
        assert rollback.json()["new_value"] == "07:30"

        show = client.get("/api/settings/show/daemon.daily_time")
        assert show.status_code == 200
        assert show.json()["value"] == "07:30"


def test_secret_like_settings_are_blocked(tmp_path):
    db_file = tmp_path / "settings_secret.db"
    with _bootstrap_client(f"sqlite:///{db_file}") as client:
        show = client.get("/api/settings/show/api_key")
        assert show.status_code == 400

        update = client.post(
            "/api/settings/update",
            json={"key": "api_key", "new_value": "sk-123", "reason": "should fail"},
        )
        assert update.status_code == 400
        assert "Secret-bearing settings" in update.text


def test_stats_path_still_works_after_settings_updates(tmp_path):
    db_file = tmp_path / "settings_stats.db"
    with _bootstrap_client(f"sqlite:///{db_file}") as client:
        update = client.post(
            "/api/settings/update",
            json={"key": "stats.default_format", "new_value": "json", "reason": "stats preference"},
        )
        assert update.status_code == 200, update.text
        assert update.json()["applied"] is True

        dashboard = client.get("/api/dashboard")
        assert dashboard.status_code == 200, dashboard.text
        payload = dashboard.json()
        assert {"cash_on_hand", "savings_funds", "liabilities_total", "net_worth"}.issubset(payload.keys())
