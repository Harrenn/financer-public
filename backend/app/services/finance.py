from __future__ import annotations

from decimal import Decimal

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from ..models import Account, Liability, Transaction
from .money import money


def _account_delta_expr(account_id: int):
    return (
        case((Transaction.to_account_id == account_id, Transaction.amount), else_=Decimal("0.00"))
        - case((Transaction.from_account_id == account_id, Transaction.amount), else_=Decimal("0.00"))
    )


def account_balance(db: Session, account: Account) -> Decimal:
    tx_sum = db.scalar(select(func.coalesce(func.sum(_account_delta_expr(account.id)), 0)))
    tx_sum_decimal = Decimal(str(tx_sum or 0))
    return money(Decimal(str(account.opening_balance)) + tx_sum_decimal)


def account_balances_map(db: Session) -> dict[int, Decimal]:
    accounts = db.scalars(select(Account)).all()
    return {a.id: account_balance(db, a) for a in accounts}


def dashboard_metrics(db: Session) -> dict[str, Decimal]:
    accounts = db.scalars(select(Account)).all()
    balances = {account.id: account_balance(db, account) for account in accounts}

    asset_balances = [
        balances[account.id]
        for account in accounts
        if account.account_type not in {"liability_credit_card", "liability_other"}
    ]

    cash_like = [
        balances[account.id]
        for account in accounts
        if account.account_type in {"bank", "digital_wallet", "cash", "savings", "fund"}
    ]

    savings_funds = [balances[account.id] for account in accounts if account.account_type in {"savings", "fund"}]

    liability_accounts = [
        balances[account.id]
        for account in accounts
        if account.account_type in {"liability_credit_card", "liability_other"}
    ]

    liability_records = db.scalars(select(Liability).where(Liability.active.is_(True))).all()
    liability_total = sum((Decimal(str(x.current_balance)) for x in liability_records), Decimal("0.00"))

    liabilities_total = money(abs(sum(liability_accounts, Decimal("0.00"))) + liability_total)
    assets_total = money(sum(asset_balances, Decimal("0.00")))

    return {
        "cash_on_hand": money(sum(cash_like, Decimal("0.00"))),
        "savings_funds": money(sum(savings_funds, Decimal("0.00"))),
        "liabilities_total": liabilities_total,
        "net_worth": money(assets_total - liabilities_total),
    }
