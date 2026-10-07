from __future__ import annotations

from decimal import Decimal

from fastapi.testclient import TestClient

from agentic_runtime import AgenticFinanceRuntime
from app.db import init_db, reset_engine, SessionLocal
from app.main import app
from app.seed import seed_defaults
from app.v2.finance import list_income_sources
from app.v2.seed import seed_defaults_v2



def _bootstrap(db_url: str) -> TestClient:
    reset_engine(db_url)
    init_db()
    db = SessionLocal()
    try:
        seed_defaults(db)
        seed_defaults_v2(db)
    finally:
        db.close()
    return TestClient(app)



def test_income_source_accepts_employment_cash_and_autocreates_cash_account(tmp_path):
    db_url = f"sqlite:///{tmp_path / 'v2_income_source.db'}"
    with _bootstrap(db_url) as client:
        response = client.post(
            "/api/income-sources",
            json={
                "name": "Primary Salary",
                "source_type": "employment",
                "payout_method": "cash",
                "is_salary": True,
                "monthly_net_amount": 31000,
                "pay_schedule_kind": "semi_monthly",
                "pay_days": [15, 30],
            },
        )
        assert response.status_code == 200, response.text
        payload = response.json()

        assert payload["source_type"]["key"] == "employment"
        assert payload["payout_method"]["key"] == "cash"
        assert payload["default_account_id"] is not None
        assert payload["monthly_net_amount"] == "31000.00"
        assert payload["pay_schedule_kind"]["key"] == "semi_monthly"
        assert payload["pay_days"] == [15, 30]

        accounts = client.get("/api/accounts")
        assert accounts.status_code == 200
        names = {item["name"] for item in accounts.json()}
        assert "Cash Wallet" in names



def test_salary_profile_payment_uses_half_for_semi_monthly(tmp_path):
    db_url = f"sqlite:///{tmp_path / 'v2_salary_payment.db'}"
    with _bootstrap(db_url) as client:
        created = client.post(
            "/api/income-sources",
            json={
                "name": "Primary Salary",
                "source_type": "employment",
                "payout_method": "cash",
                "is_salary": True,
                "monthly_net_amount": 31000,
                "pay_schedule_kind": "semi_monthly",
                "pay_days": [15, 30],
            },
        )
        assert created.status_code == 200, created.text
        source_id = created.json()["id"]

        paid = client.post("/api/salary/payments", json={"income_source_id": source_id})
        assert paid.status_code == 200, paid.text
        assert paid.json()["amount"] == "15500.00"
        assert paid.json()["kind"]["key"] == "income"



def test_dynamic_transaction_kind_with_semantic_role_flows_to_stats(tmp_path):
    db_url = f"sqlite:///{tmp_path / 'v2_dynamic_kind.db'}"
    with _bootstrap(db_url) as client:
        custom_kind = client.post(
            "/api/taxonomies",
            json={
                "domain": "transaction_kind",
                "label": "Purchase Debit",
                "semantic_role": "DEBIT_EXPENSE",
            },
        )
        assert custom_kind.status_code == 200, custom_kind.text

        account = client.post(
            "/api/accounts",
            json={
                "name": "Wallet",
                "account_type": "cash",
                "opening_balance": 1000,
            },
        )
        assert account.status_code == 200, account.text
        account_id = account.json()["id"]

        tx = client.post(
            "/api/transactions",
            json={
                "date": "2026-02-22",
                "kind": "purchase debit",
                "amount": 100,
                "from_account_id": account_id,
                "memo": "groceries",
            },
        )
        assert tx.status_code == 200, tx.text

        stats = client.get("/api/stats", params={"period": "month"})
        assert stats.status_code == 200, stats.text
        totals = stats.json()["totals"]
        assert Decimal(totals["expenses"]) >= Decimal("100")



def test_multi_agent_trace_persists_four_step_pipeline(tmp_path):
    db_url = f"sqlite:///{tmp_path / 'v2_agent_trace.db'}"
    reset_engine(db_url)
    init_db()
    db = SessionLocal()
    try:
        seed_defaults(db)
        seed_defaults_v2(db)
    finally:
        db.close()

    runtime = AgenticFinanceRuntime()
    message = runtime.handle_user_message(
        "my salary is 31000 net in a month and i get it bi-monthly every 15th and 30th in cash from employment"
    )
    assert "done" in message.lower() or "mapping" in message.lower()

    trace = runtime.get_last_trace()
    assert trace is not None
    agents = [step["agent_name"] for step in trace["steps"]]
    assert agents[:4] == ["planner", "ontology_resolver", "critic_safety", "executor"]

    db = SessionLocal()
    try:
        sources = list_income_sources(db)
        assert any(item["name"] == "Primary Salary" for item in sources)
    finally:
        db.close()
