from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from app.db import init_db, reset_engine
from app.main import app


@pytest.fixture()
def client(tmp_path):
    db_file = tmp_path / "test_finance.db"
    reset_engine(f"sqlite:///{db_file}")
    init_db()
    with TestClient(app) as test_client:
        test_client.post("/api/setup/init")
        yield test_client


def dec(value) -> Decimal:
    return Decimal(str(value))


def create_account(client: TestClient, name: str, account_type: str = "bank") -> int:
    res = client.post(
        "/api/accounts",
        json={
            "name": name,
            "account_type": account_type,
            "is_liquid": True,
            "opening_balance": "0.00",
        },
    )
    assert res.status_code == 200, res.text
    return res.json()["id"]


def create_salary_source(client: TestClient, account_id: int, frequency: str = "bi_weekly") -> int:
    res = client.post(
        "/api/income-sources",
        json={
            "name": "Main Salary",
            "source_type": "bank",
            "description": "Payroll",
            "is_salary": True,
            "frequency": frequency,
            "default_account_id": account_id,
            "active": True,
        },
    )
    assert res.status_code == 200, res.text
    return res.json()["id"]


def post_salary(client: TestClient, account_id: int, source_id: int, amount: str = "10000.00", when: str = "2026-02-01"):
    res = client.post(
        "/api/transactions",
        json={
            "date": when,
            "kind": "income",
            "amount": amount,
            "to_account_id": account_id,
            "income_source_id": source_id,
            "memo": "salary",
        },
    )
    assert res.status_code == 200, res.text
    return res.json()


def get_current_cycle(client: TestClient):
    res = client.get("/api/budget/cycle/current")
    assert res.status_code == 200, res.text
    return res.json()


def get_buckets(client: TestClient, cycle_id: int):
    res = client.get(f"/api/budget/cycle/{cycle_id}/buckets")
    assert res.status_code == 200, res.text
    return {row["bucket"]: row for row in res.json()}


def test_salary_split_50_30_20(client: TestClient):
    account_id = create_account(client, "Payroll Account")
    source_id = create_salary_source(client, account_id, "bi_weekly")
    post_salary(client, account_id, source_id, "10000.00")

    cycle = get_current_cycle(client)
    assert dec(cycle["needs_target"]) == Decimal("5000.00")
    assert dec(cycle["wants_target"]) == Decimal("3000.00")
    assert dec(cycle["savings_target"]) == Decimal("2000.00")


def test_overflow_borrows_wants_then_savings(client: TestClient):
    account_id = create_account(client, "Payroll Account")
    source_id = create_salary_source(client, account_id)

    categories = client.get("/api/categories").json()
    rent = next(item for item in categories if item["name"] == "Rent")

    rec = client.post(
        "/api/recurring-expenses",
        json={
            "name": "High Rent",
            "amount": "9000.00",
            "category_id": rent["id"],
            "due_day": 1,
            "active": True,
        },
    )
    assert rec.status_code == 200, rec.text

    post_salary(client, account_id, source_id, "10000.00", "2026-02-01")
    cycle = get_current_cycle(client)

    assert cycle["needs_overflow"] is True
    assert cycle["savings_borrowed"] is True
    assert dec(cycle["overflow_from_wants"]) == Decimal("3000.00")
    assert dec(cycle["overflow_from_savings"]) == Decimal("1000.00")


def test_salary_segregation_idempotent_manual_rerun(client: TestClient):
    account_id = create_account(client, "Payroll Account")
    source_id = create_salary_source(client, account_id)
    tx = post_salary(client, account_id, source_id, "10000.00", "2026-02-01")

    first_cycle = get_current_cycle(client)

    rerun = client.post(f"/api/salary-events/{tx['id']}/segregate")
    assert rerun.status_code == 200, rerun.text
    second_cycle = rerun.json()

    assert first_cycle["id"] == second_cycle["id"]


def test_savings_priority_routes_to_emergency_then_long_term(client: TestClient):
    payroll_account = create_account(client, "Payroll Account")
    emergency_account = create_account(client, "Emergency Savings", "savings")
    long_term_account = create_account(client, "Future Fund", "fund")
    source_id = create_salary_source(client, payroll_account)

    cfg = client.put(
        "/api/savings-priority",
        json={
            "emergency_account_id": emergency_account,
            "emergency_target_amount": "1500.00",
            "liability_priority_mode": "highest_balance",
            "long_term_savings_account_id": long_term_account,
        },
    )
    assert cfg.status_code == 200, cfg.text

    post_salary(client, payroll_account, source_id, "10000.00", "2026-02-01")
    cycle = get_current_cycle(client)

    logs = client.get(f"/api/budget/cycle/{cycle['id']}/allocation-log").json()
    emergency = [x for x in logs if x["reason_code"] == "emergency_fund"]
    long_term = [x for x in logs if x["reason_code"] == "long_term_savings"]

    assert emergency
    assert long_term
    assert dec(emergency[0]["amount"]) == Decimal("1500.00")
    assert dec(long_term[0]["amount"]) == Decimal("500.00")


def test_category_bucket_mapping_applies_spending(client: TestClient):
    account_id = create_account(client, "Payroll Account")
    source_id = create_salary_source(client, account_id)
    post_salary(client, account_id, source_id, "10000.00", "2026-02-01")

    categories = client.get("/api/categories").json()
    dining = next(item for item in categories if item["name"] == "Dining")

    expense = client.post(
        "/api/transactions",
        json={
            "date": "2026-02-03",
            "kind": "expense",
            "amount": "500.00",
            "from_account_id": account_id,
            "category_id": dining["id"],
            "memo": "Dinner",
        },
    )
    assert expense.status_code == 200, expense.text

    cycle = get_current_cycle(client)
    buckets = get_buckets(client, cycle["id"])

    assert dec(buckets["wants"]["spent"]) == Decimal("500.00")
    assert dec(buckets["needs"]["spent"]) == Decimal("0.00")


def test_recompute_archives_logs(client: TestClient):
    account_id = create_account(client, "Payroll Account")
    source_id = create_salary_source(client, account_id)
    post_salary(client, account_id, source_id, "10000.00", "2026-02-01")

    cycle = get_current_cycle(client)
    initial_logs = client.get(f"/api/budget/cycle/{cycle['id']}/allocation-log").json()
    assert len(initial_logs) > 0

    recompute = client.post(f"/api/budget/cycle/{cycle['id']}/recompute")
    assert recompute.status_code == 200, recompute.text

    logs = client.get(f"/api/budget/cycle/{cycle['id']}/allocation-log").json()
    assert any(log["is_archived"] for log in logs)
    assert any(log["recompute_version"] == 1 and not log["is_archived"] for log in logs)


def test_missing_savings_destination_creates_reminder(client: TestClient):
    account_id = create_account(client, "Payroll Account")
    source_id = create_salary_source(client, account_id)
    post_salary(client, account_id, source_id, "10000.00", str(date.today()))

    reminders = client.get("/api/reminders/upcoming")
    assert reminders.status_code == 200, reminders.text
    payload = reminders.json()
    assert any(item["kind"] == "savings_destination" for item in payload)
