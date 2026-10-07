from __future__ import annotations

import app.db as db_module
from app.seed import seed_defaults
from app.services.settings import settings_list_proposals
from agentic_runtime import AgenticFinanceRuntime


class FakeLLM:
    def __init__(self, responses: list[dict]):
        self.responses = responses
        self.timeout = 30

    def complete_json(self, **kwargs):  # noqa: ANN003
        if not self.responses:
            raise RuntimeError("No more fake responses")
        return self.responses.pop(0)


def _bootstrap_temp_db(tmp_path, name: str = "agentic_test.db") -> None:
    db_file = tmp_path / name
    db_module.reset_engine(f"sqlite:///{db_file}")
    db_module.init_db()
    db = db_module.SessionLocal()
    try:
        seed_defaults(db)
    finally:
        db.close()


def test_agent_executes_create_account_tool(tmp_path):
    _bootstrap_temp_db(tmp_path, "create_account.db")

    fake = FakeLLM(
        [
            {
                "assistant_message": "Creating the account now.",
                "tool_calls": [
                    {
                        "name": "create_account",
                        "arguments": {
                            "name": "Primary Bank",
                            "account_type": "bank",
                            "opening_balance": 1000,
                            "is_liquid": True,
                        },
                    }
                ],
                "final": False,
            },
            {
                "assistant_message": "Done. Primary Bank account is created.",
                "tool_calls": [],
                "final": True,
            },
        ]
    )

    runtime = AgenticFinanceRuntime(llm_client=fake)
    output = runtime.handle_user_message("create a primary bank account with 1000 opening balance")
    assert "created" in output.lower() or "done" in output.lower()

    accounts = runtime._tool_list_accounts({})  # noqa: SLF001
    assert any(item.name == "Primary Bank" for item in accounts)


def test_agent_can_request_deterministic_stats(tmp_path):
    _bootstrap_temp_db(tmp_path, "stats.db")

    fake = FakeLLM(
        [
            {
                "assistant_message": "Fetching monthly stats.",
                "tool_calls": [{"name": "get_stats", "arguments": {"period": "month"}}],
                "final": False,
            },
            {
                "assistant_message": "Here are your monthly stats.",
                "tool_calls": [],
                "final": True,
            },
        ]
    )

    runtime = AgenticFinanceRuntime(llm_client=fake)
    output = runtime.handle_user_message("show monthly stats")
    assert "stats" in output.lower()
    assert any(role == "tool" for role, _ in runtime.events)


def test_critical_settings_change_creates_pending_proposal(tmp_path):
    _bootstrap_temp_db(tmp_path, "settings.db")

    fake = FakeLLM(
        [
            {
                "assistant_message": "I will update model temperature.",
                "tool_calls": [
                    {
                        "name": "settings_update",
                        "arguments": {
                            "key": "model.temperature",
                            "new_value": 0.9,
                            "reason": "user requested more creative responses",
                        },
                    }
                ],
                "final": False,
            },
            {
                "assistant_message": "A confirmation proposal has been created.",
                "tool_calls": [],
                "final": True,
            },
        ]
    )

    runtime = AgenticFinanceRuntime(llm_client=fake)
    output = runtime.handle_user_message("set model temperature to 0.9")
    assert "proposal" in output.lower() or "confirmation" in output.lower()

    db = db_module.SessionLocal()
    try:
        proposals = settings_list_proposals(db, status="pending", limit=10)
        assert len(proposals) >= 1
        assert proposals[0].key == "model.temperature"
    finally:
        db.close()
