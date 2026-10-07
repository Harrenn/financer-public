from __future__ import annotations

import csv
import io
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.inspection import inspect
from sqlalchemy.orm import Session

from ..models import (
    Account,
    BackupSnapshot,
    BudgetAllocationLog,
    BudgetBucketAllocation,
    BudgetCycle,
    BudgetProfile,
    Category,
    IncomeSource,
    Liability,
    Loan,
    LoanPayment,
    ProcessedMutation,
    RecurringExpense,
    Reminder,
    RuntimeSetting,
    SavingsPriorityConfig,
    SettingsChangeProposal,
    SettingsHistory,
    SyncActivity,
    Transaction,
)
from ..seed import seed_defaults


def _to_decimal(value: Any) -> Decimal:
    return Decimal(str(value))


def _serialize(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return value


def _coerce_for_model(model_cls, record: dict[str, Any]) -> dict[str, Any]:
    mapper = inspect(model_cls)
    output: dict[str, Any] = {}
    for column in mapper.columns:
        if column.key not in record:
            continue
        value = record[column.key]
        if value is None:
            output[column.key] = None
            continue

        col_type = column.type.__class__.__name__.lower()
        if "numeric" in col_type:
            output[column.key] = _to_decimal(value)
        elif col_type == "date":
            output[column.key] = date.fromisoformat(str(value))
        elif col_type == "datetime":
            output[column.key] = datetime.fromisoformat(str(value))
        elif "boolean" in col_type:
            if isinstance(value, bool):
                output[column.key] = value
            else:
                output[column.key] = str(value).strip().lower() in {"1", "true", "yes", "on"}
        elif "integer" in col_type:
            output[column.key] = int(value)
        else:
            output[column.key] = value
    return output


def _rows_for_export(db: Session, model_cls, fields: list[str]) -> list[dict]:
    query = select(model_cls)
    if hasattr(model_cls, "id"):
        query = query.order_by(model_cls.id.asc())
    elif hasattr(model_cls, "key"):
        query = query.order_by(model_cls.key.asc())
    rows = db.scalars(query).all()
    output: list[dict] = []
    for row in rows:
        output.append({field: _serialize(getattr(row, field)) for field in fields})
    return output


def import_accounts_csv(db: Session, content: bytes) -> dict:
    rows = list(csv.DictReader(io.StringIO(content.decode("utf-8"))))
    created = 0
    errors: list[dict] = []

    for idx, row in enumerate(rows, start=2):
        try:
            with db.begin_nested():
                account = Account(
                    name=row["name"].strip(),
                    account_type=row["account_type"].strip(),
                    is_liquid=row.get("is_liquid", "true").strip().lower() in {"1", "true", "yes"},
                    opening_balance=Decimal(row.get("opening_balance", "0") or "0"),
                    currency=row.get("currency", "PHP") or "PHP",
                )
                db.add(account)
                db.flush()
            created += 1
        except Exception as exc:  # noqa: BLE001
            errors.append({"row": idx, "error": str(exc)})

    return {"created": created, "errors": errors}


def import_transactions_csv(db: Session, content: bytes) -> dict:
    rows = list(csv.DictReader(io.StringIO(content.decode("utf-8"))))
    created = 0
    errors: list[dict] = []

    categories = {c.name.lower(): c.id for c in db.scalars(select(Category)).all()}
    income_sources = {s.name.lower(): s.id for s in db.scalars(select(IncomeSource)).all()}

    for idx, row in enumerate(rows, start=2):
        try:
            category_name = (row.get("category") or "").strip().lower()
            source_name = (row.get("income_source") or "").strip().lower()
            with db.begin_nested():
                tx = Transaction(
                    date=date.fromisoformat(row["date"].strip()),
                    kind=row["kind"].strip(),
                    amount=Decimal(row["amount"].strip()),
                    from_account_id=int(row["from_account_id"]) if row.get("from_account_id") else None,
                    to_account_id=int(row["to_account_id"]) if row.get("to_account_id") else None,
                    category_id=categories.get(category_name),
                    income_source_id=income_sources.get(source_name),
                    memo=row.get("memo", ""),
                )
                db.add(tx)
                db.flush()
            created += 1
        except Exception as exc:  # noqa: BLE001
            errors.append({"row": idx, "error": str(exc)})

    return {"created": created, "errors": errors}


def export_all_data(db: Session) -> dict:
    return {
        "accounts": _rows_for_export(
            db,
            Account,
            ["id", "name", "account_type", "is_liquid", "opening_balance", "currency", "created_at"],
        ),
        "categories": _rows_for_export(db, Category, ["id", "name", "bucket_tag", "is_essential_fixed"]),
        "income_sources": _rows_for_export(
            db,
            IncomeSource,
            [
                "id",
                "name",
                "source_type",
                "description",
                "is_salary",
                "frequency",
                "default_account_id",
                "active",
            ],
        ),
        "recurring_expenses": _rows_for_export(
            db,
            RecurringExpense,
            ["id", "name", "amount", "category_id", "due_day", "target_account_id", "active"],
        ),
        "transactions": _rows_for_export(
            db,
            Transaction,
            [
                "id",
                "date",
                "kind",
                "amount",
                "from_account_id",
                "to_account_id",
                "income_source_id",
                "category_id",
                "memo",
                "created_at",
            ],
        ),
        "budget_profiles": _rows_for_export(
            db,
            BudgetProfile,
            ["id", "name", "needs_pct", "wants_pct", "savings_pct", "is_default", "active"],
        ),
        "savings_priority": _rows_for_export(
            db,
            SavingsPriorityConfig,
            ["id", "emergency_account_id", "emergency_target_amount", "liability_priority_mode", "long_term_savings_account_id"],
        ),
        "liabilities": _rows_for_export(
            db,
            Liability,
            ["id", "name", "liability_type", "account_id", "current_balance", "minimum_due", "due_day", "active"],
        ),
        "loans": _rows_for_export(
            db,
            Loan,
            ["id", "name", "principal", "rate_apr", "minimum_payment", "due_day", "status", "created_at"],
        ),
        "loan_payments": _rows_for_export(
            db,
            LoanPayment,
            ["id", "loan_id", "date", "amount", "principal_component", "interest_component", "note"],
        ),
        "reminders": _rows_for_export(
            db,
            Reminder,
            ["id", "kind", "title", "due_date", "severity", "message", "dismissed"],
        ),
        "budget_cycles": _rows_for_export(
            db,
            BudgetCycle,
            [
                "id",
                "start_date",
                "end_date",
                "source_salary_txn_id",
                "gross_inflow",
                "needs_target",
                "wants_target",
                "savings_target",
                "needs_actual",
                "wants_actual",
                "savings_actual",
                "overflow_from_wants",
                "overflow_from_savings",
                "needs_overflow",
                "savings_borrowed",
                "low_buffer",
                "status",
                "created_at",
            ],
        ),
        "budget_bucket_allocations": _rows_for_export(
            db,
            BudgetBucketAllocation,
            ["id", "cycle_id", "bucket", "target", "reserved_fixed", "spent", "remaining", "updated_at"],
        ),
        "budget_allocation_logs": _rows_for_export(
            db,
            BudgetAllocationLog,
            [
                "id",
                "cycle_id",
                "transaction_id",
                "action",
                "bucket_from",
                "bucket_to",
                "amount",
                "reason_code",
                "notes",
                "is_archived",
                "recompute_version",
                "created_at",
            ],
        ),
        "runtime_settings": _rows_for_export(
            db,
            RuntimeSetting,
            ["key", "value_json", "updated_by", "updated_at"],
        ),
        "settings_history": _rows_for_export(
            db,
            SettingsHistory,
            ["id", "key", "old_value_json", "new_value_json", "actor", "reason", "risk_level", "created_at"],
        ),
        "settings_change_proposals": _rows_for_export(
            db,
            SettingsChangeProposal,
            [
                "id",
                "key",
                "old_value_json",
                "new_value_json",
                "actor",
                "reason",
                "risk_level",
                "status",
                "created_at",
                "resolved_at",
                "resolved_by",
            ],
        ),
        "exported_at": date.today().isoformat(),
    }


def export_entity_csv(db: Session, entity: str) -> tuple[str, str]:
    definitions = {
        "accounts": (
            Account,
            ["id", "name", "account_type", "is_liquid", "opening_balance", "currency", "created_at"],
        ),
        "transactions": (
            Transaction,
            [
                "id",
                "date",
                "kind",
                "amount",
                "from_account_id",
                "to_account_id",
                "income_source_id",
                "category_id",
                "memo",
                "created_at",
            ],
        ),
        "income_sources": (
            IncomeSource,
            [
                "id",
                "name",
                "source_type",
                "description",
                "is_salary",
                "frequency",
                "default_account_id",
                "active",
            ],
        ),
        "categories": (Category, ["id", "name", "bucket_tag", "is_essential_fixed"]),
        "recurring_expenses": (
            RecurringExpense,
            ["id", "name", "amount", "category_id", "due_day", "target_account_id", "active"],
        ),
        "liabilities": (
            Liability,
            ["id", "name", "liability_type", "account_id", "current_balance", "minimum_due", "due_day", "active"],
        ),
        "loans": (
            Loan,
            ["id", "name", "principal", "rate_apr", "minimum_payment", "due_day", "status", "created_at"],
        ),
    }
    if entity not in definitions:
        raise ValueError(f"Unsupported CSV export entity: {entity}")

    model, fields = definitions[entity]
    rows = _rows_for_export(db, model, fields)

    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=fields)
    writer.writeheader()
    for row in rows:
        writer.writerow(row)

    return f"{entity}.csv", buffer.getvalue()


def reset_domain_data(db: Session, *, keep_backups: bool = True, reseed_defaults: bool = True) -> None:
    delete_order = [
        BudgetAllocationLog,
        BudgetBucketAllocation,
        BudgetCycle,
        LoanPayment,
        Transaction,
        RecurringExpense,
        Liability,
        Loan,
        IncomeSource,
        Reminder,
        Category,
        Account,
        BudgetProfile,
        SavingsPriorityConfig,
        ProcessedMutation,
        SyncActivity,
        SettingsHistory,
        SettingsChangeProposal,
        RuntimeSetting,
    ]

    for model in delete_order:
        db.execute(delete(model))

    if not keep_backups:
        db.execute(delete(BackupSnapshot))

    db.flush()
    if reseed_defaults:
        seed_defaults(db)


def import_full_replace(db: Session, payload: dict[str, Any]) -> dict:
    created: dict[str, int] = {}
    updated: dict[str, int] = {}
    skipped: dict[str, int] = {}
    errors: dict[str, int] = {}

    reset_domain_data(db, keep_backups=True, reseed_defaults=False)

    entity_order = [
        ("accounts", Account),
        ("categories", Category),
        ("income_sources", IncomeSource),
        ("recurring_expenses", RecurringExpense),
        ("transactions", Transaction),
        ("budget_profiles", BudgetProfile),
        ("savings_priority", SavingsPriorityConfig),
        ("liabilities", Liability),
        ("loans", Loan),
        ("loan_payments", LoanPayment),
        ("reminders", Reminder),
        ("budget_cycles", BudgetCycle),
        ("budget_bucket_allocations", BudgetBucketAllocation),
        ("budget_allocation_logs", BudgetAllocationLog),
        ("runtime_settings", RuntimeSetting),
        ("settings_history", SettingsHistory),
        ("settings_change_proposals", SettingsChangeProposal),
    ]

    for entity, model_cls in entity_order:
        rows = payload.get(entity, [])
        if isinstance(rows, dict):
            rows = [rows]
        if not isinstance(rows, list):
            skipped[entity] = 1
            errors[entity] = 1
            continue

        created_count = 0
        error_count = 0
        for row in rows:
            if not isinstance(row, dict):
                error_count += 1
                continue
            try:
                db.add(model_cls(**_coerce_for_model(model_cls, row)))
                created_count += 1
            except Exception:  # noqa: BLE001
                error_count += 1

        created[entity] = created_count
        updated[entity] = 0
        skipped[entity] = max(0, len(rows) - created_count - error_count)
        errors[entity] = error_count

    db.flush()
    seed_defaults(db)
    return {
        "created": created,
        "updated": updated,
        "skipped": skipped,
        "errors": errors,
    }
