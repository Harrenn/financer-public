from __future__ import annotations

import json
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import (
    Account,
    BudgetProfile,
    Category,
    IncomeSource,
    Liability,
    Loan,
    ProcessedMutation,
    RecurringExpense,
    SavingsPriorityConfig,
    SyncActivity,
    Transaction,
)
from .budget import apply_expense_to_cycle, process_salary_transaction
from .money import money


class SyncMutationError(ValueError):
    pass


def get_processed_mutation(db: Session, mutation_id: str) -> ProcessedMutation | None:
    return db.scalar(select(ProcessedMutation).where(ProcessedMutation.mutation_id == mutation_id))


def record_processed_mutation(
    db: Session,
    *,
    mutation_id: str,
    resource: str,
    op: str,
    status: str,
    message: str,
) -> ProcessedMutation:
    existing = get_processed_mutation(db, mutation_id)
    if existing:
        return existing

    row = ProcessedMutation(
        mutation_id=mutation_id,
        resource=resource,
        op=op,
        status=status,
        message=message,
    )
    db.add(row)
    return row


def record_sync_activity(
    db: Session,
    *,
    mutation_id: str,
    resource: str,
    action: str,
    conflict_fields: list[str] | None,
    details: str,
) -> SyncActivity:
    row = SyncActivity(
        mutation_id=mutation_id,
        resource=resource,
        action=action,
        conflict_fields=json.dumps(conflict_fields or []),
        details=details,
        resolution="last_write_wins",
    )
    db.add(row)
    return row


def _resolve_update_conflicts(existing, payload: dict[str, Any]) -> list[str]:
    conflicts: list[str] = []
    for key, value in payload.items():
        if not hasattr(existing, key):
            continue
        previous = getattr(existing, key)
        if str(previous) != str(value):
            conflicts.append(key)
        setattr(existing, key, value)
    return conflicts


def _create_account(db: Session, payload: dict[str, Any]) -> tuple[str, list[str], str]:
    account = Account(
        name=payload["name"],
        account_type=payload["account_type"],
        is_liquid=payload.get("is_liquid", True),
        opening_balance=money(Decimal(str(payload.get("opening_balance", "0")))),
        currency=payload.get("currency", "PHP"),
    )
    db.add(account)
    db.flush()
    return "applied", [], f"Created account {account.id}"


def _create_income_source(db: Session, payload: dict[str, Any]) -> tuple[str, list[str], str]:
    source = IncomeSource(
        name=payload["name"],
        source_type=payload["source_type"],
        description=payload.get("description", ""),
        is_salary=payload.get("is_salary", False),
        frequency=payload.get("frequency", "monthly"),
        default_account_id=payload.get("default_account_id"),
        active=payload.get("active", True),
    )
    db.add(source)
    db.flush()
    return "applied", [], f"Created income source {source.id}"


def _create_category(db: Session, payload: dict[str, Any]) -> tuple[str, list[str], str]:
    category = Category(
        name=payload["name"],
        bucket_tag=payload.get("bucket_tag", "wants"),
        is_essential_fixed=payload.get("is_essential_fixed", False),
    )
    db.add(category)
    db.flush()
    return "applied", [], f"Created category {category.id}"


def _create_recurring_expense(db: Session, payload: dict[str, Any]) -> tuple[str, list[str], str]:
    recurring = RecurringExpense(
        name=payload["name"],
        amount=money(Decimal(str(payload["amount"]))),
        category_id=int(payload["category_id"]),
        due_day=int(payload["due_day"]),
        target_account_id=payload.get("target_account_id"),
        active=payload.get("active", True),
    )
    db.add(recurring)
    db.flush()
    return "applied", [], f"Created recurring expense {recurring.id}"


def _create_liability(db: Session, payload: dict[str, Any]) -> tuple[str, list[str], str]:
    liability = Liability(
        name=payload["name"],
        liability_type=payload["liability_type"],
        account_id=payload.get("account_id"),
        current_balance=money(Decimal(str(payload.get("current_balance", "0")))),
        minimum_due=money(Decimal(str(payload.get("minimum_due", "0")))),
        due_day=int(payload["due_day"]),
        active=payload.get("active", True),
    )
    db.add(liability)
    db.flush()
    return "applied", [], f"Created liability {liability.id}"


def _create_loan(db: Session, payload: dict[str, Any]) -> tuple[str, list[str], str]:
    loan = Loan(
        name=payload["name"],
        principal=money(Decimal(str(payload["principal"]))),
        rate_apr=Decimal(str(payload["rate_apr"])) if payload.get("rate_apr") is not None else None,
        minimum_payment=money(Decimal(str(payload.get("minimum_payment", "0")))),
        due_day=int(payload["due_day"]),
        status=payload.get("status", "active"),
    )
    db.add(loan)
    db.flush()
    return "applied", [], f"Created loan {loan.id}"


def _create_transaction(db: Session, payload: dict[str, Any]) -> tuple[str, list[str], str]:
    tx_date = payload["date"]
    if isinstance(tx_date, str):
        tx_date = date.fromisoformat(tx_date)

    tx = Transaction(
        date=tx_date,
        kind=payload["kind"],
        amount=money(Decimal(str(payload["amount"]))),
        from_account_id=payload.get("from_account_id"),
        to_account_id=payload.get("to_account_id"),
        income_source_id=payload.get("income_source_id"),
        category_id=payload.get("category_id"),
        memo=payload.get("memo", ""),
    )
    db.add(tx)
    db.flush()

    if tx.kind == "income" and tx.income_source_id:
        source = db.get(IncomeSource, tx.income_source_id)
        if source and source.is_salary:
            process_salary_transaction(db, tx)

    apply_expense_to_cycle(db, tx)
    return "applied", [], f"Created transaction {tx.id}"


def _update_model(
    db: Session,
    model_cls,
    item_id: int,
    payload: dict[str, Any],
    *,
    resource_name: str,
) -> tuple[str, list[str], str]:
    existing = db.get(model_cls, item_id)
    if not existing:
        raise SyncMutationError(f"{resource_name} {item_id} not found")

    conflicts = _resolve_update_conflicts(existing, payload)
    status = "conflict_applied" if conflicts else "applied"
    return status, conflicts, f"Updated {resource_name} {item_id}"


def _update_budget_profile(db: Session, payload: dict[str, Any]) -> tuple[str, list[str], str]:
    profile = db.scalar(select(BudgetProfile).where(BudgetProfile.is_default.is_(True), BudgetProfile.active.is_(True)))
    if not profile:
        raise SyncMutationError("No active default budget profile")
    conflicts = _resolve_update_conflicts(profile, payload)
    status = "conflict_applied" if conflicts else "applied"
    return status, conflicts, "Updated default budget profile"


def _update_savings_priority(db: Session, payload: dict[str, Any]) -> tuple[str, list[str], str]:
    config = db.get(SavingsPriorityConfig, 1)
    if not config:
        config = SavingsPriorityConfig(id=1)
        db.add(config)
        db.flush()
    conflicts = _resolve_update_conflicts(config, payload)
    status = "conflict_applied" if conflicts else "applied"
    return status, conflicts, "Updated savings priority"


def apply_sync_mutation(db: Session, op: str, resource: str, payload: dict[str, Any]) -> tuple[str, list[str], str]:
    op_norm = op.upper()

    if op_norm == "POST":
        if resource == "/accounts":
            return _create_account(db, payload)
        if resource == "/income-sources":
            return _create_income_source(db, payload)
        if resource == "/categories":
            return _create_category(db, payload)
        if resource == "/recurring-expenses":
            return _create_recurring_expense(db, payload)
        if resource == "/transactions":
            return _create_transaction(db, payload)
        if resource == "/liabilities":
            return _create_liability(db, payload)
        if resource == "/loans":
            return _create_loan(db, payload)

    if op_norm in {"PATCH", "PUT"}:
        if resource == "/budget/profile":
            return _update_budget_profile(db, payload)
        if resource == "/savings-priority":
            return _update_savings_priority(db, payload)

        segments = [segment for segment in resource.split("/") if segment]
        if len(segments) == 2 and segments[1].isdigit():
            root = f"/{segments[0]}"
            item_id = int(segments[1])
            if root == "/accounts":
                return _update_model(db, Account, item_id, payload, resource_name="account")
            if root == "/income-sources":
                return _update_model(db, IncomeSource, item_id, payload, resource_name="income source")
            if root == "/categories":
                return _update_model(db, Category, item_id, payload, resource_name="category")
            if root == "/recurring-expenses":
                return _update_model(db, RecurringExpense, item_id, payload, resource_name="recurring expense")

    raise SyncMutationError(f"Unsupported mutation operation: {op_norm} {resource}")


def parse_conflict_fields(raw: str) -> list[str]:
    try:
        value = json.loads(raw)
        if isinstance(value, list):
            return [str(item) for item in value]
    except Exception:  # noqa: BLE001
        pass
    return []


def now_ts() -> datetime:
    return datetime.utcnow()
