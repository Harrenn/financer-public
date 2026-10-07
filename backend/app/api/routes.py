from __future__ import annotations

from calendar import monthrange
from datetime import date, datetime, timedelta
from decimal import Decimal

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from sqlalchemy import and_, select
from sqlalchemy.orm import Session

from ..config import (
    ACCOUNT_TYPES,
    BUCKET_TAGS,
    CURRENCY,
    INCOME_FREQUENCIES,
    INCOME_SOURCE_TYPES,
    LIABILITY_TYPES,
    LOAN_STATUSES,
    TRANSACTION_KINDS,
)
from .. import db as db_module
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
    SavingsPriorityConfig,
    SyncActivity,
    Transaction,
)
from ..schemas import (
    APIMessage,
    AccountCreate,
    AccountOut,
    AccountUpdate,
    AllocationLogOut,
    BackupSnapshotOut,
    BudgetBucketAllocationOut,
    BudgetCycleOut,
    BudgetProfileOut,
    BudgetProfileUpdate,
    CategoryCreate,
    CategoryOut,
    CategoryUpdate,
    CycleInsightsOut,
    DashboardOut,
    FactoryResetRequest,
    FactoryResetResponse,
    FullImportSummary,
    IncomeSourceCreate,
    IncomeSourceOut,
    IncomeSourceUpdate,
    LiabilityCreate,
    LiabilityOut,
    LoanCreate,
    LoanOut,
    LoanPaymentCreate,
    LoanPaymentOut,
    MutationReceipt,
    MutationEnvelope,
    SyncReplayRequest,
    SyncReplayResponse,
    SyncActivityOut,
    RecurringExpenseCreate,
    RecurringExpenseOut,
    RecurringExpenseUpdate,
    ReminderOut,
    SavingsPriorityOut,
    SavingsPriorityUpdate,
    SettingChangeRequest,
    SettingChangeResult,
    SettingsDiffOut,
    SettingsHistoryOut,
    SettingsProposalOut,
    SettingsResetRequest,
    SettingsRollbackRequest,
    SettingsValidateOut,
    RuntimeSettingOut,
    TransactionCreate,
    TransactionCompactOut,
    TransactionListOut,
    TransactionOut,
)
from ..seed import seed_defaults
from ..services.backup import create_backup_snapshot, restore_snapshot
from ..services.budget import (
    apply_expense_to_cycle,
    current_cycle,
    cycle_insights,
    process_salary_transaction,
    recompute_cycle,
)
from ..services.finance import account_balance, dashboard_metrics
from ..services.import_export import (
    export_all_data,
    export_entity_csv,
    import_accounts_csv,
    import_full_replace,
    import_transactions_csv,
    reset_domain_data,
)
from ..services.money import money
from ..services.sync import (
    SyncMutationError,
    apply_sync_mutation,
    get_processed_mutation,
    now_ts,
    parse_conflict_fields,
    record_processed_mutation,
    record_sync_activity,
)
from ..services.settings import (
    SettingsProposalError,
    SettingsValidationError,
    settings_confirm_proposal,
    settings_deny_proposal,
    settings_diff,
    settings_get,
    settings_history,
    settings_list,
    settings_list_proposals,
    settings_reset,
    settings_rollback,
    settings_update,
    settings_validate,
)

router = APIRouter(prefix="/api", tags=["api"])


def get_db() -> Session:
    db = db_module.SessionLocal()
    try:
        yield db
    finally:
        db.close()


@router.get("/health", response_model=APIMessage)
def health() -> APIMessage:
    return APIMessage(message="Smart Finance Manager API is healthy")


@router.post("/setup/init", response_model=APIMessage)
def setup_init(db: Session = Depends(get_db)) -> APIMessage:
    seed_defaults(db)
    return APIMessage(message="Database initialized with defaults")


@router.get("/dashboard", response_model=DashboardOut)
def dashboard(db: Session = Depends(get_db)) -> DashboardOut:
    return DashboardOut(**dashboard_metrics(db))


@router.get("/accounts", response_model=list[AccountOut])
def list_accounts(db: Session = Depends(get_db)) -> list[AccountOut]:
    accounts = db.scalars(select(Account).order_by(Account.id.asc())).all()
    return [
        AccountOut(
            id=account.id,
            name=account.name,
            account_type=account.account_type,
            is_liquid=account.is_liquid,
            opening_balance=money(account.opening_balance),
            currency=account.currency,
            current_balance=account_balance(db, account),
        )
        for account in accounts
    ]


@router.post("/accounts", response_model=AccountOut)
def create_account(payload: AccountCreate, db: Session = Depends(get_db)) -> AccountOut:
    if payload.account_type not in ACCOUNT_TYPES:
        raise HTTPException(status_code=400, detail="Invalid account type")

    account = Account(
        name=payload.name,
        account_type=payload.account_type,
        is_liquid=payload.is_liquid,
        opening_balance=money(payload.opening_balance),
        currency=CURRENCY,
    )
    db.add(account)
    db.commit()
    db.refresh(account)

    return AccountOut(
        id=account.id,
        name=account.name,
        account_type=account.account_type,
        is_liquid=account.is_liquid,
        opening_balance=money(account.opening_balance),
        currency=account.currency,
        current_balance=account_balance(db, account),
    )


@router.patch("/accounts/{account_id}", response_model=AccountOut)
def update_account(account_id: int, payload: AccountUpdate, db: Session = Depends(get_db)) -> AccountOut:
    account = db.get(Account, account_id)
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")

    if payload.account_type and payload.account_type not in ACCOUNT_TYPES:
        raise HTTPException(status_code=400, detail="Invalid account type")

    for field, value in payload.model_dump(exclude_none=True).items():
        setattr(account, field, value)

    db.commit()
    db.refresh(account)
    return AccountOut(
        id=account.id,
        name=account.name,
        account_type=account.account_type,
        is_liquid=account.is_liquid,
        opening_balance=money(account.opening_balance),
        currency=account.currency,
        current_balance=account_balance(db, account),
    )


@router.get("/income-sources", response_model=list[IncomeSourceOut])
def list_income_sources(db: Session = Depends(get_db)) -> list[IncomeSourceOut]:
    return [IncomeSourceOut.model_validate(item) for item in db.scalars(select(IncomeSource).order_by(IncomeSource.id.asc())).all()]


@router.post("/income-sources", response_model=IncomeSourceOut)
def create_income_source(payload: IncomeSourceCreate, db: Session = Depends(get_db)) -> IncomeSourceOut:
    if payload.source_type not in INCOME_SOURCE_TYPES:
        raise HTTPException(status_code=400, detail="Invalid source_type")
    if payload.frequency not in INCOME_FREQUENCIES:
        raise HTTPException(status_code=400, detail="Invalid frequency")

    if payload.default_account_id and not db.get(Account, payload.default_account_id):
        raise HTTPException(status_code=400, detail="default_account_id does not exist")

    source = IncomeSource(**payload.model_dump())
    db.add(source)
    db.commit()
    db.refresh(source)
    return IncomeSourceOut.model_validate(source)


@router.patch("/income-sources/{source_id}", response_model=IncomeSourceOut)
def update_income_source(source_id: int, payload: IncomeSourceUpdate, db: Session = Depends(get_db)) -> IncomeSourceOut:
    source = db.get(IncomeSource, source_id)
    if not source:
        raise HTTPException(status_code=404, detail="Income source not found")

    updates = payload.model_dump(exclude_none=True)
    if "source_type" in updates and updates["source_type"] not in INCOME_SOURCE_TYPES:
        raise HTTPException(status_code=400, detail="Invalid source_type")
    if "frequency" in updates and updates["frequency"] not in INCOME_FREQUENCIES:
        raise HTTPException(status_code=400, detail="Invalid frequency")

    for field, value in updates.items():
        setattr(source, field, value)

    db.commit()
    db.refresh(source)
    return IncomeSourceOut.model_validate(source)


@router.get("/categories", response_model=list[CategoryOut])
def list_categories(db: Session = Depends(get_db)) -> list[CategoryOut]:
    return [CategoryOut.model_validate(item) for item in db.scalars(select(Category).order_by(Category.name.asc())).all()]


@router.post("/categories", response_model=CategoryOut)
def create_category(payload: CategoryCreate, db: Session = Depends(get_db)) -> CategoryOut:
    if payload.bucket_tag not in BUCKET_TAGS:
        raise HTTPException(status_code=400, detail="Invalid bucket_tag")

    category = Category(**payload.model_dump())
    db.add(category)
    db.commit()
    db.refresh(category)
    return CategoryOut.model_validate(category)


@router.patch("/categories/{category_id}", response_model=CategoryOut)
def patch_category(category_id: int, payload: CategoryUpdate, db: Session = Depends(get_db)) -> CategoryOut:
    category = db.get(Category, category_id)
    if not category:
        raise HTTPException(status_code=404, detail="Category not found")

    updates = payload.model_dump(exclude_none=True)
    if "bucket_tag" in updates and updates["bucket_tag"] not in BUCKET_TAGS:
        raise HTTPException(status_code=400, detail="Invalid bucket_tag")

    for field, value in updates.items():
        setattr(category, field, value)

    db.commit()
    db.refresh(category)
    return CategoryOut.model_validate(category)


@router.get("/recurring-expenses", response_model=list[RecurringExpenseOut])
def list_recurring_expenses(db: Session = Depends(get_db)) -> list[RecurringExpenseOut]:
    return [
        RecurringExpenseOut.model_validate(item)
        for item in db.scalars(select(RecurringExpense).order_by(RecurringExpense.id.asc())).all()
    ]


@router.post("/recurring-expenses", response_model=RecurringExpenseOut)
def create_recurring_expense(payload: RecurringExpenseCreate, db: Session = Depends(get_db)) -> RecurringExpenseOut:
    if not db.get(Category, payload.category_id):
        raise HTTPException(status_code=400, detail="category_id does not exist")

    if payload.target_account_id and not db.get(Account, payload.target_account_id):
        raise HTTPException(status_code=400, detail="target_account_id does not exist")

    recurring = RecurringExpense(**payload.model_dump())
    db.add(recurring)
    db.commit()
    db.refresh(recurring)
    return RecurringExpenseOut.model_validate(recurring)


@router.patch("/recurring-expenses/{expense_id}", response_model=RecurringExpenseOut)
def patch_recurring_expense(expense_id: int, payload: RecurringExpenseUpdate, db: Session = Depends(get_db)) -> RecurringExpenseOut:
    recurring = db.get(RecurringExpense, expense_id)
    if not recurring:
        raise HTTPException(status_code=404, detail="Recurring expense not found")

    updates = payload.model_dump(exclude_none=True)
    if "category_id" in updates and not db.get(Category, updates["category_id"]):
        raise HTTPException(status_code=400, detail="category_id does not exist")
    if "target_account_id" in updates and updates["target_account_id"] and not db.get(Account, updates["target_account_id"]):
        raise HTTPException(status_code=400, detail="target_account_id does not exist")

    for field, value in updates.items():
        setattr(recurring, field, value)

    db.commit()
    db.refresh(recurring)
    return RecurringExpenseOut.model_validate(recurring)


@router.get("/transactions", response_model=list[TransactionOut] | TransactionListOut)
def list_transactions(
    limit: int | None = None,
    cursor: str | None = None,
    compact: bool = False,
    db: Session = Depends(get_db),
) -> list[TransactionOut] | TransactionListOut:
    base_query = select(Transaction)
    order_clause = (Transaction.date.desc(), Transaction.id.desc())

    if cursor:
        cursor_id: int | None = None
        cursor_ts: datetime | None = None

        try:
            cursor_id = int(cursor)
        except ValueError:
            try:
                cursor_ts = datetime.fromisoformat(cursor.replace("Z", "+00:00")).replace(tzinfo=None)
            except ValueError as exc:
                raise HTTPException(status_code=400, detail="cursor must be transaction id or ISO datetime") from exc

        if cursor_id is not None:
            base_query = base_query.where(Transaction.id < cursor_id)
        elif cursor_ts is not None:
            base_query = base_query.where(Transaction.created_at < cursor_ts)

    should_paginate = compact or cursor is not None or limit is not None
    if not should_paginate:
        txs = db.scalars(base_query.order_by(*order_clause)).all()
        return [TransactionOut.model_validate(tx) for tx in txs]

    page_size = 25 if limit is None else max(1, min(limit, 200))
    txs = db.scalars(base_query.order_by(*order_clause).limit(page_size + 1)).all()
    has_more = len(txs) > page_size
    current = txs[:page_size]
    next_cursor = str(current[-1].id) if has_more and current else None

    return TransactionListOut(
        items=[TransactionCompactOut.model_validate(tx) for tx in current],
        next_cursor=next_cursor,
        has_more=has_more,
    )


@router.post("/transactions", response_model=TransactionOut)
def create_transaction(payload: TransactionCreate, db: Session = Depends(get_db)) -> TransactionOut:
    if payload.kind not in TRANSACTION_KINDS:
        raise HTTPException(status_code=400, detail="Invalid transaction kind")

    if payload.category_id and not db.get(Category, payload.category_id):
        raise HTTPException(status_code=400, detail="category_id does not exist")
    if payload.income_source_id and not db.get(IncomeSource, payload.income_source_id):
        raise HTTPException(status_code=400, detail="income_source_id does not exist")

    for account_id in [payload.from_account_id, payload.to_account_id]:
        if account_id and not db.get(Account, account_id):
            raise HTTPException(status_code=400, detail=f"account_id {account_id} does not exist")

    tx = Transaction(**payload.model_dump())
    db.add(tx)
    db.flush()

    salary_cycle = None
    if tx.kind == "income" and tx.income_source_id:
        source = db.get(IncomeSource, tx.income_source_id)
        if source and source.is_salary:
            salary_cycle = process_salary_transaction(db, tx)

    apply_expense_to_cycle(db, tx)
    db.commit()
    db.refresh(tx)

    if salary_cycle:
        db.refresh(salary_cycle)

    return TransactionOut.model_validate(tx)


@router.get("/budget/profile", response_model=BudgetProfileOut)
def get_budget_profile(db: Session = Depends(get_db)) -> BudgetProfileOut:
    profile = db.scalar(select(BudgetProfile).where(BudgetProfile.is_default.is_(True), BudgetProfile.active.is_(True)))
    if not profile:
        raise HTTPException(status_code=404, detail="No active default budget profile")
    return BudgetProfileOut.model_validate(profile)


@router.put("/budget/profile", response_model=BudgetProfileOut)
def update_budget_profile(payload: BudgetProfileUpdate, db: Session = Depends(get_db)) -> BudgetProfileOut:
    profile = db.scalar(select(BudgetProfile).where(BudgetProfile.is_default.is_(True), BudgetProfile.active.is_(True)))
    if not profile:
        raise HTTPException(status_code=404, detail="No active default budget profile")

    updates = payload.model_dump(exclude_none=True)
    preview = {
        "needs_pct": Decimal(str(updates.get("needs_pct", profile.needs_pct))),
        "wants_pct": Decimal(str(updates.get("wants_pct", profile.wants_pct))),
        "savings_pct": Decimal(str(updates.get("savings_pct", profile.savings_pct))),
    }

    total = preview["needs_pct"] + preview["wants_pct"] + preview["savings_pct"]
    if money(total) != Decimal("100.00"):
        raise HTTPException(status_code=400, detail="needs_pct + wants_pct + savings_pct must equal 100")

    for field, value in updates.items():
        setattr(profile, field, value)

    db.commit()
    db.refresh(profile)
    return BudgetProfileOut.model_validate(profile)


@router.get("/budget/cycle/current", response_model=BudgetCycleOut)
def get_current_cycle(db: Session = Depends(get_db)) -> BudgetCycleOut:
    cycle = current_cycle(db)
    if not cycle:
        raise HTTPException(status_code=404, detail="No budget cycle found")
    return BudgetCycleOut.model_validate(cycle)


@router.get("/budget/cycle/{cycle_id}", response_model=BudgetCycleOut)
def get_cycle(cycle_id: int, db: Session = Depends(get_db)) -> BudgetCycleOut:
    cycle = db.get(BudgetCycle, cycle_id)
    if not cycle:
        raise HTTPException(status_code=404, detail="Budget cycle not found")
    return BudgetCycleOut.model_validate(cycle)


@router.get("/budget/cycle/{cycle_id}/buckets", response_model=list[BudgetBucketAllocationOut])
def get_cycle_buckets(cycle_id: int, db: Session = Depends(get_db)) -> list[BudgetBucketAllocationOut]:
    cycle = db.get(BudgetCycle, cycle_id)
    if not cycle:
        raise HTTPException(status_code=404, detail="Budget cycle not found")

    rows = db.scalars(
        select(BudgetBucketAllocation)
        .where(BudgetBucketAllocation.cycle_id == cycle_id)
        .order_by(BudgetBucketAllocation.bucket.asc())
    ).all()
    return [BudgetBucketAllocationOut.model_validate(row) for row in rows]


@router.get("/budget/cycle/{cycle_id}/allocation-log", response_model=list[AllocationLogOut])
def get_allocation_log(cycle_id: int, db: Session = Depends(get_db)) -> list[AllocationLogOut]:
    cycle = db.get(BudgetCycle, cycle_id)
    if not cycle:
        raise HTTPException(status_code=404, detail="Budget cycle not found")

    logs = db.scalars(
        select(BudgetAllocationLog)
        .where(BudgetAllocationLog.cycle_id == cycle_id)
        .order_by(BudgetAllocationLog.created_at.asc(), BudgetAllocationLog.id.asc())
    ).all()
    return [AllocationLogOut.model_validate(log) for log in logs]


@router.post("/budget/cycle/{cycle_id}/recompute", response_model=BudgetCycleOut)
def post_recompute_cycle(cycle_id: int, db: Session = Depends(get_db)) -> BudgetCycleOut:
    cycle = db.get(BudgetCycle, cycle_id)
    if not cycle:
        raise HTTPException(status_code=404, detail="Budget cycle not found")

    recompute_cycle(db, cycle)
    db.commit()
    db.refresh(cycle)
    return BudgetCycleOut.model_validate(cycle)


@router.post("/salary-events/{transaction_id}/segregate", response_model=BudgetCycleOut)
def manual_salary_segregate(transaction_id: int, db: Session = Depends(get_db)) -> BudgetCycleOut:
    tx = db.get(Transaction, transaction_id)
    if not tx:
        raise HTTPException(status_code=404, detail="Transaction not found")

    existing = db.scalar(select(BudgetCycle).where(BudgetCycle.source_salary_txn_id == transaction_id))
    if existing:
        recompute_cycle(db, existing)
        db.commit()
        db.refresh(existing)
        return BudgetCycleOut.model_validate(existing)

    cycle = process_salary_transaction(db, tx)
    db.commit()
    db.refresh(cycle)
    return BudgetCycleOut.model_validate(cycle)


@router.get("/insights/{cycle_id}", response_model=CycleInsightsOut)
def get_insights(cycle_id: int, db: Session = Depends(get_db)) -> CycleInsightsOut:
    cycle = db.get(BudgetCycle, cycle_id)
    if not cycle:
        raise HTTPException(status_code=404, detail="Budget cycle not found")
    return CycleInsightsOut(**cycle_insights(db, cycle))


@router.get("/reminders/upcoming", response_model=list[ReminderOut])
def get_upcoming_reminders(db: Session = Depends(get_db)) -> list[ReminderOut]:
    today = date.today()
    horizon = today + timedelta(days=7)

    dynamic: list[Reminder] = []

    recurring = db.scalars(select(RecurringExpense).where(RecurringExpense.active.is_(True))).all()
    for item in recurring:
        due = date(today.year, today.month, min(item.due_day, monthrange(today.year, today.month)[1]))
        if today <= due <= horizon:
            dynamic.append(
                Reminder(
                    id=-1,
                    kind="recurring_expense",
                    title=f"Pay {item.name}",
                    due_date=due,
                    severity="info",
                    message="Recurring expense is due soon.",
                    dismissed=False,
                )
            )

    liabilities = db.scalars(select(Liability).where(Liability.active.is_(True))).all()
    for liab in liabilities:
        due = date(today.year, today.month, min(liab.due_day, monthrange(today.year, today.month)[1]))
        if today <= due <= horizon:
            dynamic.append(
                Reminder(
                    id=-1,
                    kind="liability_due",
                    title=f"Pay {liab.name}",
                    due_date=due,
                    severity="warning",
                    message="Liability payment is due soon.",
                    dismissed=False,
                )
            )

    stored = db.scalars(
        select(Reminder)
        .where(Reminder.dismissed.is_(False), Reminder.due_date >= today, Reminder.due_date <= today + timedelta(days=30))
        .order_by(Reminder.due_date.asc())
    ).all()

    result = [ReminderOut.model_validate(item) for item in stored]
    result.extend([ReminderOut.model_validate(item) for item in dynamic])
    return sorted(result, key=lambda r: r.due_date)


@router.get("/savings-priority", response_model=SavingsPriorityOut)
def get_savings_priority(db: Session = Depends(get_db)) -> SavingsPriorityOut:
    config = db.get(SavingsPriorityConfig, 1)
    if not config:
        config = SavingsPriorityConfig(id=1)
        db.add(config)
        db.commit()
        db.refresh(config)
    return SavingsPriorityOut.model_validate(config)


@router.put("/savings-priority", response_model=SavingsPriorityOut)
def put_savings_priority(payload: SavingsPriorityUpdate, db: Session = Depends(get_db)) -> SavingsPriorityOut:
    config = db.get(SavingsPriorityConfig, 1)
    if not config:
        config = SavingsPriorityConfig(id=1)
        db.add(config)
        db.flush()

    updates = payload.model_dump(exclude_none=True)
    if "liability_priority_mode" in updates and updates["liability_priority_mode"] not in {
        "highest_balance",
        "smallest_balance",
    }:
        raise HTTPException(status_code=400, detail="liability_priority_mode must be highest_balance or smallest_balance")

    for account_field in ["emergency_account_id", "long_term_savings_account_id"]:
        if account_field in updates and updates[account_field] is not None and not db.get(Account, updates[account_field]):
            raise HTTPException(status_code=400, detail=f"{account_field} does not exist")

    for field, value in updates.items():
        setattr(config, field, value)

    db.commit()
    db.refresh(config)
    return SavingsPriorityOut.model_validate(config)


@router.get("/liabilities", response_model=list[LiabilityOut])
def list_liabilities(db: Session = Depends(get_db)) -> list[LiabilityOut]:
    return [LiabilityOut.model_validate(item) for item in db.scalars(select(Liability).order_by(Liability.id.asc())).all()]


@router.post("/liabilities", response_model=LiabilityOut)
def create_liability(payload: LiabilityCreate, db: Session = Depends(get_db)) -> LiabilityOut:
    if payload.liability_type not in LIABILITY_TYPES:
        raise HTTPException(status_code=400, detail="Invalid liability_type")
    if payload.account_id and not db.get(Account, payload.account_id):
        raise HTTPException(status_code=400, detail="account_id does not exist")

    liability = Liability(**payload.model_dump())
    db.add(liability)
    db.commit()
    db.refresh(liability)
    return LiabilityOut.model_validate(liability)


@router.get("/loans", response_model=list[LoanOut])
def list_loans(db: Session = Depends(get_db)) -> list[LoanOut]:
    return [LoanOut.model_validate(item) for item in db.scalars(select(Loan).order_by(Loan.id.asc())).all()]


@router.post("/loans", response_model=LoanOut)
def create_loan(payload: LoanCreate, db: Session = Depends(get_db)) -> LoanOut:
    if payload.status not in LOAN_STATUSES:
        raise HTTPException(status_code=400, detail="Invalid loan status")

    loan = Loan(**payload.model_dump())
    db.add(loan)
    db.commit()
    db.refresh(loan)
    return LoanOut.model_validate(loan)


@router.post("/loans/{loan_id}/payments", response_model=LoanPaymentOut)
def create_loan_payment(loan_id: int, payload: LoanPaymentCreate, db: Session = Depends(get_db)) -> LoanPaymentOut:
    loan = db.get(Loan, loan_id)
    if not loan:
        raise HTTPException(status_code=404, detail="Loan not found")

    payment = LoanPayment(loan_id=loan_id, **payload.model_dump())
    db.add(payment)

    # Basic loan tracking: reduce principal by principal component if provided, else by amount.
    reduction = payload.principal_component if payload.principal_component > 0 else payload.amount
    loan.principal = money(max(Decimal("0.00"), Decimal(str(loan.principal)) - Decimal(str(reduction))))

    db.commit()
    db.refresh(payment)
    return LoanPaymentOut.model_validate(payment)


@router.post("/import/accounts", response_model=dict)
def post_import_accounts(file: UploadFile = File(...), db: Session = Depends(get_db)) -> dict:
    if not file.filename.lower().endswith(".csv"):
        raise HTTPException(status_code=400, detail="Upload must be a CSV file")
    result = import_accounts_csv(db, file.file.read())
    db.commit()
    return result


@router.post("/import/transactions", response_model=dict)
def post_import_transactions(file: UploadFile = File(...), db: Session = Depends(get_db)) -> dict:
    if not file.filename.lower().endswith(".csv"):
        raise HTTPException(status_code=400, detail="Upload must be a CSV file")
    result = import_transactions_csv(db, file.file.read())

    # Apply new expenses to cycles where relevant.
    txs = db.scalars(select(Transaction).order_by(Transaction.id.desc()).limit(result["created"]))
    for tx in txs:
        apply_expense_to_cycle(db, tx)

    db.commit()
    return result


@router.get("/export", response_model=dict)
def get_export_data(db: Session = Depends(get_db)) -> dict:
    return export_all_data(db)


@router.get("/export/json", response_model=dict)
def get_export_data_json(db: Session = Depends(get_db)) -> dict:
    return export_all_data(db)


@router.get("/export/csv/{entity}")
def get_export_data_csv(entity: str, db: Session = Depends(get_db)) -> StreamingResponse:
    try:
        filename, content = export_entity_csv(db, entity)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return StreamingResponse(
        iter([content]),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/import/full", response_model=FullImportSummary)
def post_import_full(payload: dict, db: Session = Depends(get_db)) -> FullImportSummary:
    create_backup_snapshot(db)
    summary = import_full_replace(db, payload)
    db.commit()
    return FullImportSummary(**summary)


@router.post("/reset/factory", response_model=FactoryResetResponse)
def post_factory_reset(payload: FactoryResetRequest, db: Session = Depends(get_db)) -> FactoryResetResponse:
    if payload.confirmation_text.strip() != "RESET MY DATA":
        raise HTTPException(status_code=400, detail='confirmation_text must be exactly "RESET MY DATA"')
    if not payload.force_backup:
        raise HTTPException(status_code=400, detail="force_backup must be true")

    snapshot = create_backup_snapshot(db)
    reset_domain_data(db, keep_backups=True)
    db.commit()
    return FactoryResetResponse(
        message="Factory reset completed. Defaults have been re-seeded.",
        backup_snapshot_id=snapshot.id,
    )


@router.post("/sync/replay", response_model=SyncReplayResponse)
def post_sync_replay(payload: SyncReplayRequest, db: Session = Depends(get_db)) -> SyncReplayResponse:
    receipts: list[MutationReceipt] = []

    for envelope in payload.mutations:
        existing = get_processed_mutation(db, envelope.mutation_id)
        if existing:
            receipts.append(
                MutationReceipt(
                    mutation_id=envelope.mutation_id,
                    status="duplicate",
                    server_timestamp=now_ts(),
                    message=existing.message or "Duplicate mutation skipped",
                )
            )
            continue

        try:
            status, conflicts, message = apply_sync_mutation(db, envelope.op, envelope.resource, envelope.payload)
            record_processed_mutation(
                db,
                mutation_id=envelope.mutation_id,
                resource=envelope.resource,
                op=envelope.op.upper(),
                status=status,
                message=message,
            )
            record_sync_activity(
                db,
                mutation_id=envelope.mutation_id,
                resource=envelope.resource,
                action=status,
                conflict_fields=conflicts,
                details=message,
            )
            receipts.append(
                MutationReceipt(
                    mutation_id=envelope.mutation_id,
                    status=status,
                    server_timestamp=now_ts(),
                    message=message,
                )
            )
        except SyncMutationError as exc:
            record_sync_activity(
                db,
                mutation_id=envelope.mutation_id,
                resource=envelope.resource,
                action="failed",
                conflict_fields=[],
                details=str(exc),
            )
            receipts.append(
                MutationReceipt(
                    mutation_id=envelope.mutation_id,
                    status="failed",
                    server_timestamp=now_ts(),
                    message=str(exc),
                )
            )
        except Exception as exc:  # noqa: BLE001
            record_sync_activity(
                db,
                mutation_id=envelope.mutation_id,
                resource=envelope.resource,
                action="failed",
                conflict_fields=[],
                details=str(exc),
            )
            receipts.append(
                MutationReceipt(
                    mutation_id=envelope.mutation_id,
                    status="failed",
                    server_timestamp=now_ts(),
                    message=str(exc),
                )
            )

    db.commit()
    return SyncReplayResponse(receipts=receipts)


@router.get("/sync/activity", response_model=list[SyncActivityOut])
def get_sync_activity(db: Session = Depends(get_db)) -> list[SyncActivityOut]:
    rows = db.scalars(select(SyncActivity).order_by(SyncActivity.created_at.desc(), SyncActivity.id.desc()).limit(200)).all()
    return [
        SyncActivityOut(
            id=row.id,
            mutation_id=row.mutation_id,
            resource=row.resource,
            action=row.action,
            conflict_fields=parse_conflict_fields(row.conflict_fields),
            resolution=row.resolution,
            details=row.details,
            created_at=row.created_at,
        )
        for row in rows
    ]


@router.get("/settings", response_model=list[RuntimeSettingOut])
def get_runtime_settings(db: Session = Depends(get_db)) -> list[RuntimeSettingOut]:
    return [RuntimeSettingOut(**item) for item in settings_list(db)]


@router.get("/settings/show/{key}", response_model=RuntimeSettingOut)
def get_runtime_setting(key: str, db: Session = Depends(get_db)) -> RuntimeSettingOut:
    try:
        return RuntimeSettingOut(**settings_get(db, key))
    except SettingsValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/settings/validate", response_model=SettingsValidateOut)
def post_validate_setting(payload: SettingChangeRequest, db: Session = Depends(get_db)) -> SettingsValidateOut:
    try:
        result = settings_validate(db, payload.key, payload.new_value)
        return SettingsValidateOut(**result)
    except SettingsValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/settings/update", response_model=SettingChangeResult)
def post_update_setting(payload: SettingChangeRequest, db: Session = Depends(get_db)) -> SettingChangeResult:
    try:
        result = settings_update(db, payload.key, payload.new_value, reason=payload.reason)
        db.commit()
        return SettingChangeResult(**result)
    except SettingsValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/settings/reset/{key}", response_model=SettingChangeResult)
def post_reset_setting(key: str, payload: SettingsResetRequest, db: Session = Depends(get_db)) -> SettingChangeResult:
    try:
        result = settings_reset(db, key, reason=payload.reason)
        db.commit()
        return SettingChangeResult(**result)
    except SettingsValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/settings/diff", response_model=list[SettingsDiffOut])
def get_settings_diff(key: str | None = None, db: Session = Depends(get_db)) -> list[SettingsDiffOut]:
    try:
        rows = settings_diff(db, key=key)
        return [SettingsDiffOut(**item) for item in rows]
    except SettingsValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/settings/history", response_model=list[SettingsHistoryOut])
def get_settings_history(
    limit: int = 50,
    key: str | None = None,
    db: Session = Depends(get_db),
) -> list[SettingsHistoryOut]:
    if limit <= 0 or limit > 500:
        raise HTTPException(status_code=400, detail="limit must be between 1 and 500")

    try:
        rows = settings_history(db, limit=limit, key=key)
        return [SettingsHistoryOut.model_validate(item) for item in rows]
    except SettingsValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/settings/proposals", response_model=list[SettingsProposalOut])
def get_settings_proposals(
    status: str | None = None,
    limit: int = 50,
    db: Session = Depends(get_db),
) -> list[SettingsProposalOut]:
    if limit <= 0 or limit > 500:
        raise HTTPException(status_code=400, detail="limit must be between 1 and 500")
    rows = settings_list_proposals(db, status=status, limit=limit)
    return [SettingsProposalOut.model_validate(item) for item in rows]


@router.post("/settings/proposals/{proposal_id}/confirm", response_model=SettingChangeResult)
def post_confirm_settings_proposal(proposal_id: str, db: Session = Depends(get_db)) -> SettingChangeResult:
    try:
        result = settings_confirm_proposal(db, proposal_id)
        db.commit()
        return SettingChangeResult(**result)
    except SettingsProposalError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/settings/proposals/{proposal_id}/deny", response_model=dict)
def post_deny_settings_proposal(proposal_id: str, db: Session = Depends(get_db)) -> dict:
    try:
        result = settings_deny_proposal(db, proposal_id)
        db.commit()
        return result
    except SettingsProposalError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/settings/rollback", response_model=SettingChangeResult)
def post_settings_rollback(payload: SettingsRollbackRequest, db: Session = Depends(get_db)) -> SettingChangeResult:
    try:
        result = settings_rollback(db, payload.key, reason=payload.reason)
        db.commit()
        return SettingChangeResult(**result)
    except (SettingsProposalError, SettingsValidationError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/backups/run", response_model=BackupSnapshotOut)
def run_backup(db: Session = Depends(get_db)) -> BackupSnapshotOut:
    snapshot = create_backup_snapshot(db)
    db.commit()
    db.refresh(snapshot)
    return BackupSnapshotOut.model_validate(snapshot)


@router.get("/backups", response_model=list[BackupSnapshotOut])
def list_backups(db: Session = Depends(get_db)) -> list[BackupSnapshotOut]:
    rows = db.scalars(select(BackupSnapshot).order_by(BackupSnapshot.created_at.desc())).all()
    return [BackupSnapshotOut.model_validate(item) for item in rows]


@router.post("/backups/restore/{snapshot_id}", response_model=APIMessage)
def restore_backup(snapshot_id: int, db: Session = Depends(get_db)) -> APIMessage:
    restore_snapshot(db, snapshot_id)
    return APIMessage(message="Backup restored successfully")
