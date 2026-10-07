from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from app.db import init_db, reset_engine
from app.main import app


@pytest.fixture()
def client(tmp_path):
    db_file = tmp_path / "test_admin.db"
    reset_engine(f"sqlite:///{db_file}")
    init_db()
    with TestClient(app) as test_client:
        test_client.post("/api/setup/init")
        yield test_client


def create_account(client: TestClient, name: str) -> None:
    res = client.post(
        "/api/accounts",
        json={"name": name, "account_type": "bank", "is_liquid": True, "opening_balance": "100.00"},
    )
    assert res.status_code == 200, res.text


def test_export_json_alias(client: TestClient):
    create_account(client, "Export Test")

    export_a = client.get("/api/export")
    export_b = client.get("/api/export/json")

    assert export_a.status_code == 200
    assert export_b.status_code == 200
    assert export_a.json().keys() == export_b.json().keys()


def test_factory_reset_keeps_backups(client: TestClient):
    create_account(client, "Before Reset")

    reset_res = client.post(
        "/api/reset/factory",
        json={"confirmation_text": "RESET MY DATA", "force_backup": True},
    )
    assert reset_res.status_code == 200, reset_res.text

    accounts_after = client.get("/api/accounts")
    assert accounts_after.status_code == 200
    assert accounts_after.json() == []

    backups = client.get("/api/backups")
    assert backups.status_code == 200
    assert len(backups.json()) >= 1


def test_import_full_replace(client: TestClient):
    create_account(client, "Import Seed")
    exported = client.get("/api/export/json")
    assert exported.status_code == 200
    payload = exported.json()

    # Ensure the new import path restores a full payload after reset.
    imported = client.post("/api/import/full", json=payload)
    assert imported.status_code == 200, imported.text
    summary = imported.json()

    assert "accounts" in summary["created"]
    assert summary["errors"]["accounts"] == 0


def test_sync_replay_idempotent(client: TestClient):
    mutation_id = str(uuid.uuid4())
    replay = client.post(
        "/api/sync/replay",
        json={
            "mutations": [
                {
                    "mutation_id": mutation_id,
                    "op": "POST",
                    "resource": "/accounts",
                    "payload": {
                        "name": "Synced Account",
                        "account_type": "bank",
                        "is_liquid": True,
                        "opening_balance": "0.00",
                    },
                }
            ]
        },
    )
    assert replay.status_code == 200, replay.text
    assert replay.json()["receipts"][0]["status"] in {"applied", "conflict_applied"}

    replay_dupe = client.post(
        "/api/sync/replay",
        json={
            "mutations": [
                {
                    "mutation_id": mutation_id,
                    "op": "POST",
                    "resource": "/accounts",
                    "payload": {
                        "name": "Synced Account",
                        "account_type": "bank",
                        "is_liquid": True,
                        "opening_balance": "0.00",
                    },
                }
            ]
        },
    )
    assert replay_dupe.status_code == 200, replay_dupe.text
    assert replay_dupe.json()["receipts"][0]["status"] == "duplicate"

    activity = client.get("/api/sync/activity")
    assert activity.status_code == 200
    assert len(activity.json()) >= 1


def test_transactions_compact_cursor_pagination(client: TestClient):
    account = client.post(
        "/api/accounts",
        json={"name": "Pager Account", "account_type": "bank", "is_liquid": True, "opening_balance": "0.00"},
    )
    assert account.status_code == 200, account.text
    account_id = account.json()["id"]

    for idx in range(3):
        created = client.post(
            "/api/transactions",
            json={
                "date": "2026-02-01",
                "kind": "expense",
                "amount": "10.00",
                "from_account_id": account_id,
                "memo": f"expense-{idx}",
            },
        )
        assert created.status_code == 200, created.text

    page_1 = client.get("/api/transactions", params={"compact": 1, "limit": 2})
    assert page_1.status_code == 200, page_1.text
    first_payload = page_1.json()

    assert isinstance(first_payload["items"], list)
    assert len(first_payload["items"]) == 2
    assert first_payload["has_more"] is True
    assert first_payload["next_cursor"] is not None

    page_2 = client.get(
        "/api/transactions",
        params={"compact": 1, "limit": 2, "cursor": first_payload["next_cursor"]},
    )
    assert page_2.status_code == 200, page_2.text
    second_payload = page_2.json()

    assert len(second_payload["items"]) == 1
    assert second_payload["has_more"] is False


def test_transactions_default_response_stays_list(client: TestClient):
    account = client.post(
        "/api/accounts",
        json={"name": "Default List Account", "account_type": "bank", "is_liquid": True, "opening_balance": "0.00"},
    )
    assert account.status_code == 200, account.text
    account_id = account.json()["id"]

    created = client.post(
        "/api/transactions",
        json={
            "date": "2026-02-01",
            "kind": "expense",
            "amount": "10.00",
            "from_account_id": account_id,
            "memo": "list-shape",
        },
    )
    assert created.status_code == 200, created.text

    response = client.get("/api/transactions")
    assert response.status_code == 200, response.text
    payload = response.json()
    assert isinstance(payload, list)
    assert payload[0]["memo"] == "list-shape"
