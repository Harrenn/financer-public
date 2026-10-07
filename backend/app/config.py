from __future__ import annotations

from decimal import Decimal
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
BACKUP_DIR = DATA_DIR / "backups"
DB_PATH = DATA_DIR / "finance_v2.db"
DATABASE_URL = f"sqlite:///{DB_PATH}"

CURRENCY = "PHP"

DEFAULT_BUDGET_PROFILE = {
    "name": "Default 50/30/20",
    "needs_pct": Decimal("50.00"),
    "wants_pct": Decimal("30.00"),
    "savings_pct": Decimal("20.00"),
}

ACCOUNT_TYPES = {
    "bank",
    "digital_wallet",
    "savings",
    "fund",
    "cash",
    "liability_credit_card",
    "liability_other",
}

INCOME_SOURCE_TYPES = {"bank", "digital_app", "cash", "other"}
INCOME_FREQUENCIES = {"weekly", "bi_weekly", "semi_monthly", "monthly"}

TRANSACTION_KINDS = {"income", "expense", "transfer", "liability_payment"}
BUCKET_TAGS = {"needs", "wants", "savings"}
LIABILITY_TYPES = {"credit_card", "loan", "other"}
LOAN_STATUSES = {"active", "closed", "paused"}

DEFAULT_CATEGORIES = [
    {"name": "Rent", "bucket_tag": "needs", "is_essential_fixed": True},
    {"name": "Utilities", "bucket_tag": "needs", "is_essential_fixed": True},
    {"name": "Groceries", "bucket_tag": "needs", "is_essential_fixed": False},
    {"name": "Transportation", "bucket_tag": "needs", "is_essential_fixed": False},
    {"name": "Health", "bucket_tag": "needs", "is_essential_fixed": False},
    {"name": "Dining", "bucket_tag": "wants", "is_essential_fixed": False},
    {"name": "Shopping", "bucket_tag": "wants", "is_essential_fixed": False},
    {"name": "Entertainment", "bucket_tag": "wants", "is_essential_fixed": False},
    {"name": "Savings Allocation", "bucket_tag": "savings", "is_essential_fixed": False},
]

DEFAULT_SAVINGS_PRIORITY = {
    "liability_priority_mode": "highest_balance",
    "emergency_target_amount": Decimal("0.00"),
}

BACKUP_RETENTION = 14

# Non-secret runtime settings that the terminal agent can modify autonomously.
DEFAULT_RUNTIME_SETTINGS: dict[str, object] = {
    "daemon.daily_time": "08:00",
    "daemon.weekly_day": "monday",
    "daemon.weekly_time": "08:00",
    "daemon.monthly_day": 1,
    "daemon.monthly_time": "08:00",
    "daemon.annual_month": 1,
    "daemon.annual_day": 1,
    "daemon.annual_time": "08:00",
    "policy.large_amount_confirmation_threshold": 1000.0,
    "policy.large_amount_confirmation_hard_cap": 10000.0,
    "alerts.channel": "terminal_inbox",
    "stats.default_format": "table",
    "stats.default_scope": "month",
    "analytics.forecast_sensitivity": "medium",
    "analytics.anomaly_sensitivity": "medium",
    "model.name": "gemini-2.5-flash",
    "model.temperature": 0.2,
    "model.top_p": 0.95,
    "model.retry_limit": 3,
    "model.timeout_seconds": 30,
    "model.planner.name": "gemini-2.5-flash",
    "model.planner.temperature": 0.2,
    "model.planner.top_p": 0.95,
    "model.planner.retry_limit": 3,
    "model.planner.timeout_seconds": 30,
    "model.ontology.name": "gemini-2.5-flash",
    "model.ontology.temperature": 0.1,
    "model.ontology.top_p": 0.9,
    "model.ontology.retry_limit": 2,
    "model.ontology.timeout_seconds": 20,
    "model.critic.name": "gemini-2.5-flash",
    "model.critic.temperature": 0.0,
    "model.critic.top_p": 0.8,
    "model.critic.retry_limit": 2,
    "model.critic.timeout_seconds": 20,
    "model.executor.name": "deterministic",
    "model.executor.temperature": 0.0,
    "model.executor.top_p": 1.0,
    "model.executor.retry_limit": 0,
    "model.executor.timeout_seconds": 15,
    "multi_agent.autocreate_confidence_threshold": 0.85,
    "multi_agent.followup_confidence_threshold": 0.6,
    "multi_agent.enabled": True,
    "safety.tool_allowlist": [],
    "safety.tool_denylist": [],
    "safety.max_actions_per_turn": 8,
    "safety.autonomous_depth_limit": 6,
}
