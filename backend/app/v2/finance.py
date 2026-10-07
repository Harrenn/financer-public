from __future__ import annotations

import json
from calendar import monthrange
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import and_, select
from sqlalchemy.orm import Session

from .models import Account, AgentRun, AgentRunStep, IncomeSource, Liability, TaxonomyValue, Transaction
from .taxonomy import (
    create_taxonomy_alias,
    create_taxonomy_value,
    resolve_taxonomy_term,
    taxonomy_value_to_dict,
)


MONEY_QUANT = Decimal("0.01")


def money(value: Decimal | str | float | int) -> Decimal:
    return Decimal(str(value)).quantize(MONEY_QUANT)


def _json_default(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(money(value))
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return str(value)


def _json_dumps(payload: dict[str, Any]) -> str:
    return json.dumps(payload, separators=(",", ":"), ensure_ascii=True, default=_json_default)


def _pay_days_from_json(raw: str) -> list[int]:
    try:
        parsed = json.loads(raw or "[]")
    except json.JSONDecodeError:
        return []
    out: list[int] = []
    seen: set[int] = set()
    for item in parsed:
        try:
            day = int(item)
        except (TypeError, ValueError):
            continue
        if day < 1 or day > 31 or day in seen:
            continue
        seen.add(day)
        out.append(day)
    return sorted(out)


def _pay_days_to_json(pay_days: list[int]) -> str:
    cleaned = sorted({int(day) for day in pay_days if 1 <= int(day) <= 31})
    return json.dumps(cleaned, separators=(",", ":"))


def _taxonomy_key(db: Session, taxonomy_id: int | None) -> str | None:
    if not taxonomy_id:
        return None
    item = db.get(TaxonomyValue, taxonomy_id)
    return item.key if item else None


def _taxonomy_semantic_role(db: Session, taxonomy_id: int | None) -> str | None:
    if not taxonomy_id:
        return None
    item = db.get(TaxonomyValue, taxonomy_id)
    return item.semantic_role if item else None


def account_current_balance(db: Session, account: Account) -> Decimal:
    balance = money(account.opening_balance)
    txs = db.scalars(
        select(Transaction)
        .where(
            (Transaction.from_account_id == account.id) | (Transaction.to_account_id == account.id)
        )
        .order_by(Transaction.date.asc(), Transaction.id.asc())
    ).all()

    for tx in txs:
        amount = money(tx.amount)
        if tx.to_account_id == account.id:
            balance += amount
        if tx.from_account_id == account.id:
            balance -= amount
    return money(balance)


def list_accounts(db: Session) -> list[dict[str, Any]]:
    rows = db.scalars(select(Account).order_by(Account.id.asc())).all()
    output: list[dict[str, Any]] = []
    for row in rows:
        output.append(
            {
                "id": row.id,
                "name": row.name,
                "account_type": taxonomy_value_to_dict(row.account_type),
                "is_liquid": row.is_liquid,
                "opening_balance": money(row.opening_balance),
                "currency": row.currency,
                "current_balance": account_current_balance(db, row),
            }
        )
    return output


def create_account(
    db: Session,
    *,
    name: str,
    account_type: str | int,
    is_liquid: bool = True,
    opening_balance: Decimal = Decimal("0.00"),
) -> dict[str, Any]:
    resolved = resolve_taxonomy_term(
        db,
        domain="account_type",
        term=account_type,
        autocreate=True,
        created_by="executor",
    )
    if not resolved.resolved or not resolved.value:
        raise ValueError("Unable to resolve account_type")

    existing = db.scalar(select(Account).where(Account.name == name.strip()))
    if existing:
        existing.account_type_id = resolved.value.id
        existing.is_liquid = is_liquid
        existing.opening_balance = money(opening_balance)
        db.flush()
        account = existing
    else:
        account = Account(
            name=name.strip(),
            account_type_id=resolved.value.id,
            is_liquid=is_liquid,
            opening_balance=money(opening_balance),
            currency="PHP",
        )
        db.add(account)
        db.flush()

    return {
        "id": account.id,
        "name": account.name,
        "account_type": taxonomy_value_to_dict(account.account_type),
        "is_liquid": account.is_liquid,
        "opening_balance": money(account.opening_balance),
        "currency": account.currency,
        "current_balance": account_current_balance(db, account),
    }


def ensure_cash_account(db: Session, *, name_hint: str = "Cash Wallet") -> dict[str, Any]:
    account_type = resolve_taxonomy_term(
        db,
        domain="account_type",
        term="cash",
        semantic_role="CASH_HOLDING",
        autocreate=True,
        created_by="executor",
    )
    if not account_type.resolved or not account_type.value:
        raise ValueError("Unable to resolve cash account type")

    account = db.scalar(
        select(Account).where(
            and_(
                Account.account_type_id == account_type.value.id,
                Account.name == name_hint,
            )
        )
    )
    if not account:
        account = Account(
            name=name_hint,
            account_type_id=account_type.value.id,
            is_liquid=True,
            opening_balance=Decimal("0.00"),
            currency="PHP",
        )
        db.add(account)
        db.flush()

    return {
        "id": account.id,
        "name": account.name,
        "account_type": taxonomy_value_to_dict(account.account_type),
        "is_liquid": account.is_liquid,
        "opening_balance": money(account.opening_balance),
        "currency": account.currency,
        "current_balance": account_current_balance(db, account),
    }


def list_income_sources(db: Session) -> list[dict[str, Any]]:
    rows = db.scalars(select(IncomeSource).order_by(IncomeSource.id.asc())).all()
    output: list[dict[str, Any]] = []
    for row in rows:
        output.append(
            {
                "id": row.id,
                "name": row.name,
                "source_type": taxonomy_value_to_dict(row.source_type),
                "payout_method": taxonomy_value_to_dict(row.payout_method) if row.payout_method else None,
                "is_salary": row.is_salary,
                "default_account_id": row.default_account_id,
                "monthly_net_amount": money(row.monthly_net_amount) if row.monthly_net_amount is not None else None,
                "pay_schedule_kind": taxonomy_value_to_dict(row.pay_schedule_kind) if row.pay_schedule_kind else None,
                "pay_days": _pay_days_from_json(row.pay_days_json),
                "active": row.active,
                "created_at": row.created_at,
            }
        )
    return output


def upsert_income_source_with_salary_profile(
    db: Session,
    *,
    name: str,
    source_type: str | int,
    payout_method: str | int | None = None,
    is_salary: bool = False,
    default_account_id: int | None = None,
    monthly_net_amount: Decimal | None = None,
    pay_schedule_kind: str | int | None = None,
    pay_days: list[int] | None = None,
    active: bool = True,
) -> dict[str, Any]:
    source_type_res = resolve_taxonomy_term(
        db,
        domain="income_source_type",
        term=source_type,
        semantic_role="INCOME_SOURCE",
        autocreate=True,
        created_by="executor",
    )
    if not source_type_res.resolved or not source_type_res.value:
        raise ValueError("Unable to resolve source_type")

    payout_id: int | None = None
    payout_value_key: str | None = None
    if payout_method is not None:
        payout_res = resolve_taxonomy_term(
            db,
            domain="payout_method",
            term=payout_method,
            semantic_role="PAYOUT_METHOD",
            autocreate=True,
            created_by="executor",
        )
        if not payout_res.resolved or not payout_res.value:
            raise ValueError("Unable to resolve payout_method")
        payout_id = payout_res.value.id
        payout_value_key = payout_res.value.key

    schedule_id: int | None = None
    if pay_schedule_kind is not None:
        schedule_res = resolve_taxonomy_term(
            db,
            domain="pay_schedule_kind",
            term=pay_schedule_kind,
            semantic_role="PAY_SCHEDULE",
            autocreate=True,
            created_by="executor",
        )
        if not schedule_res.resolved or not schedule_res.value:
            raise ValueError("Unable to resolve pay_schedule_kind")
        schedule_id = schedule_res.value.id

    if default_account_id is None and payout_value_key == "cash":
        cash = ensure_cash_account(db, name_hint="Cash Wallet")
        default_account_id = cash["id"]

    if default_account_id is not None and not db.get(Account, default_account_id):
        raise ValueError("default_account_id does not exist")

    existing = db.scalar(select(IncomeSource).where(IncomeSource.name == name.strip()))
    payload_days = pay_days or []

    if existing:
        existing.source_type_id = source_type_res.value.id
        existing.payout_method_id = payout_id
        existing.is_salary = is_salary
        existing.default_account_id = default_account_id
        existing.monthly_net_amount = money(monthly_net_amount) if monthly_net_amount is not None else None
        existing.pay_schedule_kind_id = schedule_id
        existing.pay_days_json = _pay_days_to_json(payload_days)
        existing.active = active
        db.flush()
        source = existing
    else:
        source = IncomeSource(
            name=name.strip(),
            source_type_id=source_type_res.value.id,
            payout_method_id=payout_id,
            is_salary=is_salary,
            default_account_id=default_account_id,
            monthly_net_amount=money(monthly_net_amount) if monthly_net_amount is not None else None,
            pay_schedule_kind_id=schedule_id,
            pay_days_json=_pay_days_to_json(payload_days),
            active=active,
        )
        db.add(source)
        db.flush()

    return {
        "id": source.id,
        "name": source.name,
        "source_type": taxonomy_value_to_dict(source.source_type),
        "payout_method": taxonomy_value_to_dict(source.payout_method) if source.payout_method else None,
        "is_salary": source.is_salary,
        "default_account_id": source.default_account_id,
        "monthly_net_amount": money(source.monthly_net_amount) if source.monthly_net_amount is not None else None,
        "pay_schedule_kind": taxonomy_value_to_dict(source.pay_schedule_kind) if source.pay_schedule_kind else None,
        "pay_days": _pay_days_from_json(source.pay_days_json),
        "active": source.active,
        "created_at": source.created_at,
    }


def create_transaction(
    db: Session,
    *,
    date_value: date,
    kind: str | int,
    amount: Decimal,
    from_account_id: int | None = None,
    to_account_id: int | None = None,
    income_source_id: int | None = None,
    category_id: int | None = None,
    memo: str = "",
) -> dict[str, Any]:
    kind_res = resolve_taxonomy_term(
        db,
        domain="transaction_kind",
        term=kind,
        autocreate=True,
        created_by="executor",
    )
    if not kind_res.resolved or not kind_res.value:
        raise ValueError("Unable to resolve transaction kind")

    semantic = (kind_res.value.semantic_role or "").upper()
    if semantic == "CREDIT_INFLOW" and not to_account_id:
        raise ValueError("income transactions require to_account_id")
    if semantic in {"DEBIT_EXPENSE", "DEBT_PAYMENT"} and not from_account_id:
        raise ValueError("expense/liability transactions require from_account_id")
    if semantic == "TRANSFER_INTERNAL" and (not from_account_id or not to_account_id):
        raise ValueError("transfer transactions require from_account_id and to_account_id")

    if from_account_id and not db.get(Account, from_account_id):
        raise ValueError("from_account_id does not exist")
    if to_account_id and not db.get(Account, to_account_id):
        raise ValueError("to_account_id does not exist")
    if income_source_id and not db.get(IncomeSource, income_source_id):
        raise ValueError("income_source_id does not exist")

    tx = Transaction(
        date=date_value,
        kind_id=kind_res.value.id,
        amount=money(amount),
        from_account_id=from_account_id,
        to_account_id=to_account_id,
        income_source_id=income_source_id,
        category_id=category_id,
        memo=memo,
    )
    db.add(tx)
    db.flush()

    return {
        "id": tx.id,
        "date": tx.date,
        "kind": taxonomy_value_to_dict(tx.kind),
        "amount": money(tx.amount),
        "from_account_id": tx.from_account_id,
        "to_account_id": tx.to_account_id,
        "income_source_id": tx.income_source_id,
        "category_id": tx.category_id,
        "memo": tx.memo,
        "created_at": tx.created_at,
    }


def record_salary_payment_from_profile(
    db: Session,
    *,
    income_source_id: int,
    date_value: date | None = None,
    amount: Decimal | None = None,
    to_account_id: int | None = None,
    memo: str = "salary payment",
) -> dict[str, Any]:
    source = db.get(IncomeSource, income_source_id)
    if not source:
        raise ValueError("income source not found")
    if not source.is_salary:
        raise ValueError("income source is not marked as salary")

    payout_key = _taxonomy_key(db, source.payout_method_id)
    destination = to_account_id or source.default_account_id
    if destination is None and payout_key == "cash":
        destination = ensure_cash_account(db, name_hint="Cash Wallet")["id"]

    if destination is None:
        raise ValueError("salary destination account is not configured")

    final_amount: Decimal
    if amount is not None:
        final_amount = money(amount)
    elif source.monthly_net_amount is not None:
        schedule_key = _taxonomy_key(db, source.pay_schedule_kind_id)
        pay_days = _pay_days_from_json(source.pay_days_json)
        if schedule_key == "semi_monthly" and len(pay_days) == 2:
            final_amount = money(Decimal(str(source.monthly_net_amount)) / Decimal("2"))
        else:
            final_amount = money(source.monthly_net_amount)
    else:
        raise ValueError("amount is required when monthly_net_amount is not configured")

    return create_transaction(
        db,
        date_value=date_value or date.today(),
        kind="income",
        amount=final_amount,
        to_account_id=destination,
        income_source_id=source.id,
        memo=memo,
    )


def dashboard_metrics(db: Session) -> dict[str, Decimal]:
    accounts = db.scalars(select(Account).order_by(Account.id.asc())).all()
    liabilities = db.scalars(select(Liability)).all()

    cash_on_hand = Decimal("0.00")
    savings_funds = Decimal("0.00")

    for account in accounts:
        balance = account_current_balance(db, account)
        key = _taxonomy_key(db, account.account_type_id)
        if key in {"cash", "bank", "digital_wallet"}:
            cash_on_hand += balance
        elif key in {"savings", "fund"}:
            savings_funds += balance
        else:
            cash_on_hand += balance

    liabilities_total = sum((money(item.current_balance) for item in liabilities), Decimal("0.00"))
    net_worth = money(cash_on_hand + savings_funds - liabilities_total)

    return {
        "cash_on_hand": money(cash_on_hand),
        "savings_funds": money(savings_funds),
        "liabilities_total": money(liabilities_total),
        "net_worth": net_worth,
    }


def _period_bounds(period: str, start_date: date | None, end_date: date | None) -> tuple[date, date]:
    today = date.today()
    normalized = period.strip().lower()

    if normalized == "custom":
        if not start_date or not end_date:
            raise ValueError("custom period requires start_date and end_date")
        if end_date < start_date:
            raise ValueError("end_date must be >= start_date")
        return start_date, end_date

    if normalized == "day":
        return today, today
    if normalized == "week":
        start = today - timedelta(days=today.weekday())
        return start, start + timedelta(days=6)
    if normalized == "month":
        start = date(today.year, today.month, 1)
        end = date(today.year, today.month, monthrange(today.year, today.month)[1])
        return start, end
    if normalized == "year":
        return date(today.year, 1, 1), date(today.year, 12, 31)

    raise ValueError("period must be one of: day, week, month, year, custom")


def compute_stats(
    db: Session,
    *,
    period: str = "month",
    start_date: date | None = None,
    end_date: date | None = None,
) -> dict[str, Any]:
    start, end = _period_bounds(period, start_date, end_date)
    txs = db.scalars(
        select(Transaction)
        .where(Transaction.date >= start, Transaction.date <= end)
        .order_by(Transaction.date.asc(), Transaction.id.asc())
    ).all()

    income_total = Decimal("0.00")
    expense_total = Decimal("0.00")
    transfer_total = Decimal("0.00")
    debt_payment_total = Decimal("0.00")

    by_kind: dict[str, Decimal] = {}

    for tx in txs:
        role = (_taxonomy_semantic_role(db, tx.kind_id) or "").upper()
        key = (_taxonomy_key(db, tx.kind_id) or "unknown")
        amount = money(tx.amount)
        by_kind[key] = by_kind.get(key, Decimal("0.00")) + amount

        if role == "CREDIT_INFLOW":
            income_total += amount
        elif role == "DEBIT_EXPENSE":
            expense_total += amount
        elif role == "TRANSFER_INTERNAL":
            transfer_total += amount
        elif role == "DEBT_PAYMENT":
            debt_payment_total += amount
            expense_total += amount

    return {
        "period": period,
        "start_date": start,
        "end_date": end,
        "transaction_count": len(txs),
        "totals": {
            "income": money(income_total),
            "expenses": money(expense_total),
            "transfer_volume": money(transfer_total),
            "debt_payments": money(debt_payment_total),
            "net_cash_flow": money(income_total - expense_total),
        },
        "breakdowns": {
            "by_kind": [
                {"kind": kind, "amount": money(value)}
                for kind, value in sorted(by_kind.items(), key=lambda item: item[0])
            ]
        },
    }


def create_agent_run(db: Session, user_input: str) -> AgentRun:
    row = AgentRun(user_input=user_input, assistant_output="", status="started")
    db.add(row)
    db.flush()
    return row


def create_agent_run_step(
    db: Session,
    *,
    run_id: int,
    step_order: int,
    agent_name: str,
    input_payload: dict[str, Any],
    output_payload: dict[str, Any],
    status: str = "ok",
    error: str | None = None,
) -> AgentRunStep:
    row = AgentRunStep(
        run_id=run_id,
        step_order=step_order,
        agent_name=agent_name,
        input_json=_json_dumps(input_payload),
        output_json=_json_dumps(output_payload),
        status=status,
        error=error,
    )
    db.add(row)
    db.flush()
    return row


def finalize_agent_run(db: Session, run_id: int, *, assistant_output: str, status: str) -> None:
    row = db.get(AgentRun, run_id)
    if not row:
        return
    row.assistant_output = assistant_output
    row.status = status
    db.flush()


def latest_agent_run(db: Session) -> dict[str, Any] | None:
    run = db.scalar(select(AgentRun).order_by(AgentRun.id.desc()))
    if not run:
        return None

    steps = db.scalars(
        select(AgentRunStep)
        .where(AgentRunStep.run_id == run.id)
        .order_by(AgentRunStep.step_order.asc(), AgentRunStep.id.asc())
    ).all()

    out_steps: list[dict[str, Any]] = []
    for step in steps:
        try:
            input_json = json.loads(step.input_json or "{}")
        except json.JSONDecodeError:
            input_json = {}
        try:
            output_json = json.loads(step.output_json or "{}")
        except json.JSONDecodeError:
            output_json = {}

        out_steps.append(
            {
                "id": step.id,
                "step_order": step.step_order,
                "agent_name": step.agent_name,
                "status": step.status,
                "error": step.error,
                "input_json": input_json,
                "output_json": output_json,
                "created_at": step.created_at,
            }
        )

    return {
        "id": run.id,
        "user_input": run.user_input,
        "assistant_output": run.assistant_output,
        "status": run.status,
        "created_at": run.created_at,
        "steps": out_steps,
    }
