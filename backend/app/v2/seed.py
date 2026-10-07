from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import TaxonomyAlias, TaxonomyValue
from .taxonomy import create_taxonomy_alias, create_taxonomy_value


SYSTEM_TAXONOMIES: list[dict] = [
    {
        "domain": "account_type",
        "items": [
            {"key": "cash", "label": "Cash", "semantic_role": "CASH_HOLDING", "aliases": ["wallet", "cash wallet"]},
            {"key": "bank", "label": "Bank", "semantic_role": "BANK_HOLDING", "aliases": ["bank account"]},
            {
                "key": "digital_wallet",
                "label": "Digital Wallet",
                "semantic_role": "DIGITAL_HOLDING",
                "aliases": ["gcash", "maya", "ewallet"],
            },
            {"key": "savings", "label": "Savings", "semantic_role": "SAVINGS_HOLDING", "aliases": ["emergency fund"]},
            {"key": "fund", "label": "Fund", "semantic_role": "SAVINGS_HOLDING", "aliases": ["investment fund"]},
            {"key": "liability", "label": "Liability", "semantic_role": "LIABILITY_ACCOUNT", "aliases": []},
        ],
    },
    {
        "domain": "income_source_type",
        "items": [
            {"key": "employment", "label": "Employment", "semantic_role": "INCOME_SOURCE", "aliases": ["job", "salary"]},
            {"key": "freelance", "label": "Freelance", "semantic_role": "INCOME_SOURCE", "aliases": ["contract"]},
            {"key": "business", "label": "Business", "semantic_role": "INCOME_SOURCE", "aliases": []},
            {"key": "investment", "label": "Investment", "semantic_role": "INCOME_SOURCE", "aliases": []},
            {"key": "other", "label": "Other", "semantic_role": "INCOME_SOURCE", "aliases": []},
        ],
    },
    {
        "domain": "payout_method",
        "items": [
            {"key": "cash", "label": "Cash", "semantic_role": "PAYOUT_METHOD", "aliases": ["physical cash"]},
            {"key": "bank_transfer", "label": "Bank Transfer", "semantic_role": "PAYOUT_METHOD", "aliases": ["bank", "wire"]},
            {
                "key": "digital_wallet",
                "label": "Digital Wallet",
                "semantic_role": "PAYOUT_METHOD",
                "aliases": ["e-wallet", "gcash", "maya"],
            },
            {"key": "check", "label": "Check", "semantic_role": "PAYOUT_METHOD", "aliases": ["cheque"]},
            {"key": "other", "label": "Other", "semantic_role": "PAYOUT_METHOD", "aliases": []},
        ],
    },
    {
        "domain": "transaction_kind",
        "items": [
            {"key": "income", "label": "Income", "semantic_role": "CREDIT_INFLOW", "aliases": ["salary credit", "inflow"]},
            {"key": "expense", "label": "Expense", "semantic_role": "DEBIT_EXPENSE", "aliases": ["spend", "purchase"]},
            {
                "key": "transfer",
                "label": "Transfer",
                "semantic_role": "TRANSFER_INTERNAL",
                "aliases": ["move funds", "internal transfer"],
            },
            {
                "key": "liability_payment",
                "label": "Liability Payment",
                "semantic_role": "DEBT_PAYMENT",
                "aliases": ["loan payment", "debt payment"],
            },
        ],
    },
    {
        "domain": "bucket_type",
        "items": [
            {"key": "needs", "label": "Needs", "semantic_role": "NEEDS", "aliases": ["essentials"]},
            {"key": "wants", "label": "Wants", "semantic_role": "WANTS", "aliases": ["lifestyle"]},
            {"key": "savings", "label": "Savings", "semantic_role": "SAVINGS", "aliases": ["save"]},
        ],
    },
    {
        "domain": "liability_type",
        "items": [
            {"key": "credit_card", "label": "Credit Card", "semantic_role": "LIABILITY", "aliases": ["cc"]},
            {"key": "loan", "label": "Loan", "semantic_role": "LIABILITY", "aliases": []},
            {"key": "other", "label": "Other", "semantic_role": "LIABILITY", "aliases": []},
        ],
    },
    {
        "domain": "loan_status",
        "items": [
            {"key": "active", "label": "Active", "semantic_role": "STATUS_ACTIVE", "aliases": []},
            {"key": "closed", "label": "Closed", "semantic_role": "STATUS_CLOSED", "aliases": []},
            {"key": "paused", "label": "Paused", "semantic_role": "STATUS_PAUSED", "aliases": []},
        ],
    },
    {
        "domain": "pay_schedule_kind",
        "items": [
            {"key": "weekly", "label": "Weekly", "semantic_role": "PAY_SCHEDULE", "aliases": []},
            {"key": "bi_weekly", "label": "Bi-Weekly", "semantic_role": "PAY_SCHEDULE", "aliases": ["biweekly"]},
            {
                "key": "semi_monthly",
                "label": "Semi-Monthly",
                "semantic_role": "PAY_SCHEDULE",
                "aliases": ["bi-monthly", "twice a month"],
            },
            {"key": "monthly", "label": "Monthly", "semantic_role": "PAY_SCHEDULE", "aliases": []},
            {"key": "custom_days", "label": "Custom Days", "semantic_role": "PAY_SCHEDULE", "aliases": []},
        ],
    },
]


def seed_defaults_v2(db: Session) -> None:
    for group in SYSTEM_TAXONOMIES:
        domain = group["domain"]
        for item in group["items"]:
            existing = db.scalar(
                select(TaxonomyValue).where(
                    TaxonomyValue.domain == domain,
                    TaxonomyValue.key == item["key"],
                )
            )
            if not existing:
                existing = create_taxonomy_value(
                    db,
                    domain=domain,
                    key=item["key"],
                    label=item["label"],
                    semantic_role=item.get("semantic_role"),
                    is_system=True,
                    metadata={"seeded": True},
                )

            aliases = {alias.alias for alias in db.scalars(select(TaxonomyAlias).where(TaxonomyAlias.taxonomy_value_id == existing.id)).all()}
            for alias in item.get("aliases", []):
                normalized = alias.strip().lower()
                if normalized in aliases:
                    continue
                create_taxonomy_alias(
                    db,
                    domain=domain,
                    alias=alias,
                    taxonomy_value_id=existing.id,
                    confidence=1.0,
                    created_by="system_seed",
                )

    db.commit()
