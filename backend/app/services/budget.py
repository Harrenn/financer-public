from __future__ import annotations

from calendar import monthrange
from datetime import date
from decimal import Decimal

from sqlalchemy import and_, select
from sqlalchemy.orm import Session

from ..models import (
    Account,
    BudgetAllocationLog,
    BudgetBucketAllocation,
    BudgetCycle,
    BudgetProfile,
    Category,
    IncomeSource,
    Liability,
    RecurringExpense,
    Reminder,
    SavingsPriorityConfig,
    Transaction,
)
from .dates import compute_cycle_bounds
from .finance import account_balance
from .money import money


def default_profile(db: Session) -> BudgetProfile:
    profile = db.scalar(select(BudgetProfile).where(BudgetProfile.is_default.is_(True), BudgetProfile.active.is_(True)))
    if profile:
        return profile
    profile = db.scalar(select(BudgetProfile).where(BudgetProfile.active.is_(True)).order_by(BudgetProfile.id.asc()))
    if not profile:
        raise ValueError("No active budget profile configured")
    return profile


def _split_salary(amount: Decimal, profile: BudgetProfile) -> tuple[Decimal, Decimal, Decimal]:
    needs = money(amount * Decimal(str(profile.needs_pct)) / Decimal("100"))
    wants = money(amount * Decimal(str(profile.wants_pct)) / Decimal("100"))
    savings = money(amount - needs - wants)
    return needs, wants, savings


def _alloc_map(cycle: BudgetCycle) -> dict[str, BudgetBucketAllocation]:
    return {alloc.bucket: alloc for alloc in cycle.allocations}


def _log(
    db: Session,
    *,
    cycle_id: int,
    action: str,
    amount: Decimal,
    reason_code: str,
    bucket_from: str | None = None,
    bucket_to: str | None = None,
    notes: str = "",
    transaction_id: int | None = None,
    recompute_version: int = 0,
) -> BudgetAllocationLog:
    record = BudgetAllocationLog(
        cycle_id=cycle_id,
        action=action,
        amount=money(amount),
        reason_code=reason_code,
        bucket_from=bucket_from,
        bucket_to=bucket_to,
        notes=notes,
        transaction_id=transaction_id,
        recompute_version=recompute_version,
    )
    db.add(record)
    return record


def _months_between(start: date, end: date) -> list[tuple[int, int]]:
    output: list[tuple[int, int]] = []
    year, month = start.year, start.month
    while (year, month) <= (end.year, end.month):
        output.append((year, month))
        if month == 12:
            year += 1
            month = 1
        else:
            month += 1
    return output


def _due_dates_within_cycle(start: date, end: date, due_day: int) -> list[date]:
    dates: list[date] = []
    for y, m in _months_between(start, end):
        safe_day = min(due_day, monthrange(y, m)[1])
        due_date = date(y, m, safe_day)
        if start <= due_date <= end:
            dates.append(due_date)
    return dates


def _ensure_missing_destination_reminder(db: Session, cycle: BudgetCycle) -> None:
    title = f"Configure savings destination for cycle {cycle.id}"
    existing = db.scalar(
        select(Reminder).where(
            Reminder.kind == "savings_destination",
            Reminder.title == title,
            Reminder.dismissed.is_(False),
        )
    )
    if existing:
        return
    reminder = Reminder(
        kind="savings_destination",
        title=title,
        due_date=cycle.end_date,
        severity="warning",
        message="Savings bucket has no configured destination account.",
    )
    db.add(reminder)


def _reserve_fixed_expenses(
    db: Session,
    cycle: BudgetCycle,
    allocations: dict[str, BudgetBucketAllocation],
    *,
    recompute_version: int,
) -> None:
    needs = allocations["needs"]
    wants = allocations["wants"]
    savings = allocations["savings"]

    recurring = db.scalars(select(RecurringExpense).where(RecurringExpense.active.is_(True))).all()
    total_due = Decimal("0.00")
    for item in recurring:
        category = db.get(Category, item.category_id)
        if not category:
            continue
        if category.bucket_tag != "needs" or not category.is_essential_fixed:
            continue
        occurrences = _due_dates_within_cycle(cycle.start_date, cycle.end_date, item.due_day)
        if not occurrences:
            continue
        total_due += Decimal(str(item.amount)) * len(occurrences)

    total_due = money(total_due)
    if total_due <= 0:
        return

    shortfall = max(Decimal("0.00"), total_due - Decimal(str(needs.remaining)))
    if shortfall > 0:
        cycle.needs_overflow = True
        cycle.low_buffer = True

        from_wants = min(shortfall, Decimal(str(wants.remaining)))
        if from_wants > 0:
            wants.remaining = money(Decimal(str(wants.remaining)) - from_wants)
            needs.remaining = money(Decimal(str(needs.remaining)) + from_wants)
            cycle.overflow_from_wants = money(Decimal(str(cycle.overflow_from_wants)) + from_wants)
            _log(
                db,
                cycle_id=cycle.id,
                action="overflow_transfer",
                amount=from_wants,
                reason_code="needs_shortfall",
                bucket_from="wants",
                bucket_to="needs",
                recompute_version=recompute_version,
            )
            shortfall -= from_wants

        from_savings = min(shortfall, Decimal(str(savings.remaining)))
        if from_savings > 0:
            savings.remaining = money(Decimal(str(savings.remaining)) - from_savings)
            needs.remaining = money(Decimal(str(needs.remaining)) + from_savings)
            cycle.overflow_from_savings = money(Decimal(str(cycle.overflow_from_savings)) + from_savings)
            cycle.savings_borrowed = True
            _log(
                db,
                cycle_id=cycle.id,
                action="overflow_transfer",
                amount=from_savings,
                reason_code="needs_shortfall",
                bucket_from="savings",
                bucket_to="needs",
                recompute_version=recompute_version,
            )
            shortfall -= from_savings

    reserve_amount = min(total_due, Decimal(str(needs.remaining)))
    needs.reserved_fixed = money(Decimal(str(needs.reserved_fixed)) + reserve_amount)
    needs.remaining = money(Decimal(str(needs.remaining)) - reserve_amount)
    _log(
        db,
        cycle_id=cycle.id,
        action="reserve_fixed",
        amount=reserve_amount,
        reason_code="fixed_expense_reservation",
        bucket_from="needs",
        bucket_to="needs",
        recompute_version=recompute_version,
    )

    if reserve_amount < total_due:
        _ensure_missing_destination_reminder(db, cycle)


def _route_savings(
    db: Session,
    cycle: BudgetCycle,
    allocations: dict[str, BudgetBucketAllocation],
    *,
    recompute_version: int,
) -> None:
    savings = allocations["savings"]
    remaining = Decimal(str(savings.remaining))
    if remaining <= 0:
        return

    config = db.get(SavingsPriorityConfig, 1)
    if not config:
        config = SavingsPriorityConfig(id=1)
        db.add(config)
        db.flush()

    if config.emergency_account_id and Decimal(str(config.emergency_target_amount)) > 0:
        emergency_account = db.get(Account, config.emergency_account_id)
        if emergency_account:
            current_balance = account_balance(db, emergency_account)
            target = Decimal(str(config.emergency_target_amount))
            gap = max(Decimal("0.00"), target - current_balance)
            allocate = min(gap, remaining)
            if allocate > 0:
                remaining -= allocate
                _log(
                    db,
                    cycle_id=cycle.id,
                    action="savings_route",
                    amount=allocate,
                    reason_code="emergency_fund",
                    bucket_from="savings",
                    bucket_to="savings",
                    notes=f"Emergency account {config.emergency_account_id}",
                    recompute_version=recompute_version,
                )

    if remaining > 0:
        liabilities = db.scalars(select(Liability).where(Liability.active.is_(True))).all()
        if config.liability_priority_mode == "smallest_balance":
            liabilities.sort(key=lambda x: Decimal(str(x.current_balance)))
        else:
            liabilities.sort(key=lambda x: Decimal(str(x.current_balance)), reverse=True)

        for item in liabilities:
            if remaining <= 0:
                break
            outstanding = max(Decimal("0.00"), Decimal(str(item.current_balance)))
            if outstanding <= 0:
                continue
            allocate = min(outstanding, remaining)
            remaining -= allocate
            _log(
                db,
                cycle_id=cycle.id,
                action="savings_route",
                amount=allocate,
                reason_code="liability_paydown",
                bucket_from="savings",
                bucket_to="savings",
                notes=f"Liability {item.id}",
                recompute_version=recompute_version,
            )

    if remaining > 0:
        if config.long_term_savings_account_id:
            _log(
                db,
                cycle_id=cycle.id,
                action="savings_route",
                amount=remaining,
                reason_code="long_term_savings",
                bucket_from="savings",
                bucket_to="savings",
                notes=f"Account {config.long_term_savings_account_id}",
                recompute_version=recompute_version,
            )
            remaining = Decimal("0.00")
        else:
            _ensure_missing_destination_reminder(db, cycle)
            _log(
                db,
                cycle_id=cycle.id,
                action="savings_route",
                amount=remaining,
                reason_code="missing_destination",
                bucket_from="savings",
                bucket_to="savings",
                notes="No long-term savings destination configured",
                recompute_version=recompute_version,
            )

    savings.remaining = money(remaining)


def _bucket_for_transaction(db: Session, tx: Transaction) -> str:
    if tx.kind == "liability_payment":
        return "savings"
    if not tx.category_id:
        return "wants"
    category = db.get(Category, tx.category_id)
    if not category:
        return "wants"
    return category.bucket_tag or "wants"


def _apply_spending_transaction(
    db: Session,
    cycle: BudgetCycle,
    allocations: dict[str, BudgetBucketAllocation],
    tx: Transaction,
    *,
    recompute_version: int,
) -> None:
    bucket = _bucket_for_transaction(db, tx)
    alloc = allocations.get(bucket)
    if not alloc:
        return

    amount = Decimal(str(tx.amount))
    alloc.spent = money(Decimal(str(alloc.spent)) + amount)
    alloc.remaining = money(Decimal(str(alloc.remaining)) - amount)
    if Decimal(str(alloc.remaining)) < 0:
        cycle.low_buffer = True

    _log(
        db,
        cycle_id=cycle.id,
        action="expense_apply",
        amount=amount,
        reason_code="transaction_spend",
        bucket_from=bucket,
        bucket_to=bucket,
        transaction_id=tx.id,
        recompute_version=recompute_version,
    )


def _apply_spending_range(
    db: Session,
    cycle: BudgetCycle,
    allocations: dict[str, BudgetBucketAllocation],
    *,
    recompute_version: int,
) -> None:
    txs = db.scalars(
        select(Transaction)
        .where(
            Transaction.date >= cycle.start_date,
            Transaction.date <= cycle.end_date,
            Transaction.kind.in_(["expense", "liability_payment"]),
        )
        .order_by(Transaction.date.asc(), Transaction.id.asc())
    ).all()
    for tx in txs:
        _apply_spending_transaction(db, cycle, allocations, tx, recompute_version=recompute_version)


def _update_cycle_actuals(cycle: BudgetCycle, allocations: dict[str, BudgetBucketAllocation]) -> None:
    cycle.needs_actual = money(allocations["needs"].remaining)
    cycle.wants_actual = money(allocations["wants"].remaining)
    cycle.savings_actual = money(allocations["savings"].remaining)


def cycle_for_date(db: Session, when: date) -> BudgetCycle | None:
    return db.scalar(
        select(BudgetCycle)
        .where(BudgetCycle.start_date <= when, BudgetCycle.end_date >= when)
        .order_by(BudgetCycle.start_date.desc())
    )


def current_cycle(db: Session) -> BudgetCycle | None:
    today = date.today()
    cycle = cycle_for_date(db, today)
    if cycle:
        return cycle
    return db.scalar(select(BudgetCycle).order_by(BudgetCycle.start_date.desc()))


def process_salary_transaction(db: Session, salary_tx: Transaction, *, recompute_version: int = 0) -> BudgetCycle:
    existing = db.scalar(select(BudgetCycle).where(BudgetCycle.source_salary_txn_id == salary_tx.id))
    if existing:
        return existing

    if salary_tx.kind != "income" or not salary_tx.income_source_id:
        raise ValueError("Salary transaction must be income with an income source")

    source = db.get(IncomeSource, salary_tx.income_source_id)
    if not source or not source.is_salary:
        raise ValueError("Income source is not configured as salary")

    profile = default_profile(db)
    inflow = money(Decimal(str(salary_tx.amount)))
    needs, wants, savings = _split_salary(inflow, profile)
    start_date, end_date = compute_cycle_bounds(salary_tx.date, source.frequency)

    cycle = BudgetCycle(
        start_date=start_date,
        end_date=end_date,
        source_salary_txn_id=salary_tx.id,
        gross_inflow=inflow,
        needs_target=needs,
        wants_target=wants,
        savings_target=savings,
        needs_actual=needs,
        wants_actual=wants,
        savings_actual=savings,
    )
    db.add(cycle)
    db.flush()

    for bucket, target in (("needs", needs), ("wants", wants), ("savings", savings)):
        db.add(
            BudgetBucketAllocation(
                cycle_id=cycle.id,
                bucket=bucket,
                target=target,
                remaining=target,
                reserved_fixed=Decimal("0.00"),
                spent=Decimal("0.00"),
            )
        )

    db.flush()
    db.refresh(cycle)
    allocations = _alloc_map(cycle)

    _reserve_fixed_expenses(db, cycle, allocations, recompute_version=recompute_version)
    _route_savings(db, cycle, allocations, recompute_version=recompute_version)
    _apply_spending_range(db, cycle, allocations, recompute_version=recompute_version)
    _update_cycle_actuals(cycle, allocations)

    return cycle


def apply_expense_to_cycle(db: Session, tx: Transaction) -> None:
    if tx.kind not in {"expense", "liability_payment"}:
        return
    cycle = cycle_for_date(db, tx.date)
    if not cycle:
        return

    allocations = _alloc_map(cycle)
    _apply_spending_transaction(db, cycle, allocations, tx, recompute_version=0)
    _update_cycle_actuals(cycle, allocations)


def recompute_cycle(db: Session, cycle: BudgetCycle) -> BudgetCycle:
    version = (
        db.scalar(
            select(BudgetAllocationLog.recompute_version)
            .where(BudgetAllocationLog.cycle_id == cycle.id)
            .order_by(BudgetAllocationLog.recompute_version.desc())
        )
        or 0
    ) + 1

    logs = db.scalars(select(BudgetAllocationLog).where(BudgetAllocationLog.cycle_id == cycle.id)).all()
    for log in logs:
        log.is_archived = True

    cycle.needs_overflow = False
    cycle.savings_borrowed = False
    cycle.low_buffer = False
    cycle.overflow_from_wants = Decimal("0.00")
    cycle.overflow_from_savings = Decimal("0.00")

    allocations = _alloc_map(cycle)
    allocations["needs"].reserved_fixed = Decimal("0.00")
    allocations["needs"].spent = Decimal("0.00")
    allocations["needs"].remaining = Decimal(str(cycle.needs_target))

    allocations["wants"].reserved_fixed = Decimal("0.00")
    allocations["wants"].spent = Decimal("0.00")
    allocations["wants"].remaining = Decimal(str(cycle.wants_target))

    allocations["savings"].reserved_fixed = Decimal("0.00")
    allocations["savings"].spent = Decimal("0.00")
    allocations["savings"].remaining = Decimal(str(cycle.savings_target))

    _reserve_fixed_expenses(db, cycle, allocations, recompute_version=version)
    _route_savings(db, cycle, allocations, recompute_version=version)
    _apply_spending_range(db, cycle, allocations, recompute_version=version)
    _update_cycle_actuals(cycle, allocations)

    return cycle


def cycle_insights(db: Session, cycle: BudgetCycle) -> dict:
    allocations = _alloc_map(cycle)

    needs_used = Decimal(str(allocations["needs"].reserved_fixed)) + Decimal(str(allocations["needs"].spent))
    wants_used = Decimal(str(allocations["wants"].spent))
    savings_used = Decimal(str(cycle.savings_target)) - Decimal(str(allocations["savings"].remaining))

    needs_drift = money(needs_used - Decimal(str(cycle.needs_target)))
    wants_drift = money(wants_used - Decimal(str(cycle.wants_target)))
    savings_drift = money(savings_used - Decimal(str(cycle.savings_target)))

    total_drift = max(Decimal("0.00"), needs_drift) + max(Decimal("0.00"), wants_drift) + max(Decimal("0.00"), savings_drift)
    gross = Decimal(str(cycle.gross_inflow)) or Decimal("1.00")
    compliance = money(max(Decimal("0.00"), Decimal("100.00") - (total_drift / gross * Decimal("100"))))

    warnings: list[str] = []
    if cycle.needs_overflow:
        warnings.append("Needs bucket exceeded target; overflow borrowing applied.")
    if cycle.savings_borrowed:
        warnings.append("Savings bucket borrowed to cover essential expenses.")
    if cycle.low_buffer:
        warnings.append("One or more bucket balances are below safe buffer.")

    missing_destination = db.scalar(
        select(Reminder).where(
            Reminder.kind == "savings_destination",
            Reminder.dismissed.is_(False),
            and_(Reminder.due_date >= cycle.start_date, Reminder.due_date <= cycle.end_date),
        )
    )
    if missing_destination:
        warnings.append("Savings destination is not configured.")

    return {
        "cycle_id": cycle.id,
        "needs_drift": needs_drift,
        "wants_drift": wants_drift,
        "savings_drift": savings_drift,
        "compliance_pct": compliance,
        "warnings": warnings,
    }
