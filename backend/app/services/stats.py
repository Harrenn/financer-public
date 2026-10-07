from __future__ import annotations

from calendar import monthrange
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Account, Category, Transaction
from .finance import dashboard_metrics
from .money import money


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
        end = start + timedelta(days=6)
        return start, end

    if normalized == "month":
        start = date(today.year, today.month, 1)
        end = date(today.year, today.month, monthrange(today.year, today.month)[1])
        return start, end

    if normalized == "year":
        return date(today.year, 1, 1), date(today.year, 12, 31)

    raise ValueError("period must be one of: day, week, month, year, custom")


def _decimal_to_float(value: Decimal) -> float:
    return float(money(value))


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

    categories = {row.id: row.name for row in db.scalars(select(Category)).all()}
    accounts = {row.id: row.name for row in db.scalars(select(Account)).all()}

    income_total = Decimal("0.00")
    expense_total = Decimal("0.00")
    transfer_total = Decimal("0.00")
    liability_payment_total = Decimal("0.00")

    by_kind: dict[str, Decimal] = {}
    by_category: dict[str, Decimal] = {}
    account_net_flow: dict[str, Decimal] = {}

    for tx in txs:
        amount = Decimal(str(tx.amount))
        by_kind[tx.kind] = by_kind.get(tx.kind, Decimal("0.00")) + amount

        if tx.kind == "income":
            income_total += amount
        elif tx.kind == "expense":
            expense_total += amount
        elif tx.kind == "transfer":
            transfer_total += amount
        elif tx.kind == "liability_payment":
            liability_payment_total += amount
            expense_total += amount

        category_name = categories.get(tx.category_id, "uncategorized")
        if tx.kind in {"expense", "liability_payment"}:
            by_category[category_name] = by_category.get(category_name, Decimal("0.00")) + amount

        if tx.to_account_id:
            key = accounts.get(tx.to_account_id, f"account:{tx.to_account_id}")
            account_net_flow[key] = account_net_flow.get(key, Decimal("0.00")) + amount
        if tx.from_account_id:
            key = accounts.get(tx.from_account_id, f"account:{tx.from_account_id}")
            account_net_flow[key] = account_net_flow.get(key, Decimal("0.00")) - amount

    net_cash_flow = income_total - expense_total
    dashboard = dashboard_metrics(db)

    by_kind_rows = [
        {"kind": kind, "amount": _decimal_to_float(amount)}
        for kind, amount in sorted(by_kind.items(), key=lambda item: item[0])
    ]
    by_category_rows = [
        {"category": name, "amount": _decimal_to_float(amount)}
        for name, amount in sorted(by_category.items(), key=lambda item: item[1], reverse=True)
    ]
    account_flow_rows = [
        {"account": name, "net_flow": _decimal_to_float(amount)}
        for name, amount in sorted(account_net_flow.items(), key=lambda item: abs(item[1]), reverse=True)
    ]

    return {
        "period": period,
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "transaction_count": len(txs),
        "totals": {
            "income": _decimal_to_float(income_total),
            "expenses": _decimal_to_float(expense_total),
            "transfer_volume": _decimal_to_float(transfer_total),
            "liability_payments": _decimal_to_float(liability_payment_total),
            "net_cash_flow": _decimal_to_float(net_cash_flow),
        },
        "breakdowns": {
            "by_kind": by_kind_rows,
            "by_category": by_category_rows,
            "account_net_flow": account_flow_rows,
        },
        "portfolio_snapshot": {
            "cash_on_hand": _decimal_to_float(Decimal(str(dashboard["cash_on_hand"]))),
            "savings_funds": _decimal_to_float(Decimal(str(dashboard["savings_funds"]))),
            "liabilities_total": _decimal_to_float(Decimal(str(dashboard["liabilities_total"]))),
            "net_worth": _decimal_to_float(Decimal(str(dashboard["net_worth"]))),
        },
    }
