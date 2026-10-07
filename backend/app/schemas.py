from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator


class APIMessage(BaseModel):
    message: str


class AccountBase(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    account_type: str
    is_liquid: bool = True
    opening_balance: Decimal = Decimal("0.00")


class AccountCreate(AccountBase):
    pass


class AccountUpdate(BaseModel):
    name: Optional[str] = None
    account_type: Optional[str] = None
    is_liquid: Optional[bool] = None


class AccountOut(AccountBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    currency: str
    current_balance: Decimal


class IncomeSourceBase(BaseModel):
    name: str
    source_type: str
    description: str = ""
    is_salary: bool = False
    frequency: str = "monthly"
    default_account_id: Optional[int] = None
    active: bool = True


class IncomeSourceCreate(IncomeSourceBase):
    pass


class IncomeSourceUpdate(BaseModel):
    name: Optional[str] = None
    source_type: Optional[str] = None
    description: Optional[str] = None
    is_salary: Optional[bool] = None
    frequency: Optional[str] = None
    default_account_id: Optional[int] = None
    active: Optional[bool] = None


class IncomeSourceOut(IncomeSourceBase):
    model_config = ConfigDict(from_attributes=True)

    id: int


class CategoryBase(BaseModel):
    name: str
    bucket_tag: str = "wants"
    is_essential_fixed: bool = False


class CategoryCreate(CategoryBase):
    pass


class CategoryUpdate(BaseModel):
    name: Optional[str] = None
    bucket_tag: Optional[str] = None
    is_essential_fixed: Optional[bool] = None


class CategoryOut(CategoryBase):
    model_config = ConfigDict(from_attributes=True)

    id: int


class RecurringExpenseBase(BaseModel):
    name: str
    amount: Decimal
    category_id: int
    due_day: int = Field(ge=1, le=31)
    target_account_id: Optional[int] = None
    active: bool = True


class RecurringExpenseCreate(RecurringExpenseBase):
    pass


class RecurringExpenseUpdate(BaseModel):
    name: Optional[str] = None
    amount: Optional[Decimal] = None
    category_id: Optional[int] = None
    due_day: Optional[int] = Field(default=None, ge=1, le=31)
    target_account_id: Optional[int] = None
    active: Optional[bool] = None


class RecurringExpenseOut(RecurringExpenseBase):
    model_config = ConfigDict(from_attributes=True)

    id: int


class TransactionBase(BaseModel):
    date: date
    kind: str
    amount: Decimal = Field(gt=0)
    from_account_id: Optional[int] = None
    to_account_id: Optional[int] = None
    income_source_id: Optional[int] = None
    category_id: Optional[int] = None
    memo: str = ""

    @model_validator(mode="after")
    def validate_direction(self) -> "TransactionBase":
        if self.kind == "income" and not self.to_account_id:
            raise ValueError("income transactions require to_account_id")
        if self.kind in {"expense", "liability_payment"} and not self.from_account_id:
            raise ValueError("expense/liability_payment transactions require from_account_id")
        if self.kind == "transfer" and (not self.from_account_id or not self.to_account_id):
            raise ValueError("transfer requires both from_account_id and to_account_id")
        return self


class TransactionCreate(TransactionBase):
    pass


class TransactionOut(TransactionBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    created_at: datetime


class TransactionCompactOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    date: date
    kind: str
    amount: Decimal
    from_account_id: Optional[int]
    to_account_id: Optional[int]
    income_source_id: Optional[int]
    category_id: Optional[int]
    memo: str
    created_at: datetime


class TransactionListOut(BaseModel):
    items: list[TransactionCompactOut]
    next_cursor: Optional[str] = None
    has_more: bool = False


class BudgetProfileOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    needs_pct: Decimal
    wants_pct: Decimal
    savings_pct: Decimal
    is_default: bool
    active: bool


class BudgetProfileUpdate(BaseModel):
    name: Optional[str] = None
    needs_pct: Optional[Decimal] = None
    wants_pct: Optional[Decimal] = None
    savings_pct: Optional[Decimal] = None


class BudgetBucketAllocationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    cycle_id: int
    bucket: str
    target: Decimal
    reserved_fixed: Decimal
    spent: Decimal
    remaining: Decimal


class BudgetCycleOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    start_date: date
    end_date: date
    source_salary_txn_id: int
    gross_inflow: Decimal
    needs_target: Decimal
    wants_target: Decimal
    savings_target: Decimal
    needs_actual: Decimal
    wants_actual: Decimal
    savings_actual: Decimal
    overflow_from_wants: Decimal
    overflow_from_savings: Decimal
    needs_overflow: bool
    savings_borrowed: bool
    low_buffer: bool
    status: str


class AllocationLogOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    cycle_id: int
    transaction_id: Optional[int]
    action: str
    bucket_from: Optional[str]
    bucket_to: Optional[str]
    amount: Decimal
    reason_code: str
    notes: str
    recompute_version: int
    is_archived: bool
    created_at: datetime


class SavingsPriorityUpdate(BaseModel):
    emergency_account_id: Optional[int] = None
    emergency_target_amount: Optional[Decimal] = None
    liability_priority_mode: Optional[str] = None
    long_term_savings_account_id: Optional[int] = None


class SavingsPriorityOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    emergency_account_id: Optional[int]
    emergency_target_amount: Decimal
    liability_priority_mode: str
    long_term_savings_account_id: Optional[int]


class LiabilityBase(BaseModel):
    name: str
    liability_type: str
    account_id: Optional[int] = None
    current_balance: Decimal = Decimal("0.00")
    minimum_due: Decimal = Decimal("0.00")
    due_day: int = Field(ge=1, le=31)
    active: bool = True


class LiabilityCreate(LiabilityBase):
    pass


class LiabilityOut(LiabilityBase):
    model_config = ConfigDict(from_attributes=True)

    id: int


class LoanBase(BaseModel):
    name: str
    principal: Decimal
    rate_apr: Optional[Decimal] = None
    minimum_payment: Decimal = Decimal("0.00")
    due_day: int = Field(ge=1, le=31)
    status: str = "active"


class LoanCreate(LoanBase):
    pass


class LoanOut(LoanBase):
    model_config = ConfigDict(from_attributes=True)

    id: int


class LoanPaymentCreate(BaseModel):
    date: date
    amount: Decimal = Field(gt=0)
    principal_component: Decimal = Decimal("0.00")
    interest_component: Decimal = Decimal("0.00")
    note: str = ""


class LoanPaymentOut(LoanPaymentCreate):
    model_config = ConfigDict(from_attributes=True)

    id: int
    loan_id: int


class ReminderOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    kind: str
    title: str
    due_date: date
    severity: str
    message: str
    dismissed: bool


class DashboardOut(BaseModel):
    cash_on_hand: Decimal
    savings_funds: Decimal
    liabilities_total: Decimal
    net_worth: Decimal


class CycleInsightsOut(BaseModel):
    cycle_id: int
    needs_drift: Decimal
    wants_drift: Decimal
    savings_drift: Decimal
    compliance_pct: Decimal
    warnings: list[str]


class BackupSnapshotOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    created_at: datetime
    path: str
    size_bytes: int
    sha256: str


class FullImportSummary(BaseModel):
    created: dict[str, int]
    updated: dict[str, int]
    skipped: dict[str, int]
    errors: dict[str, int]


class FactoryResetRequest(BaseModel):
    confirmation_text: str
    force_backup: bool = True


class FactoryResetResponse(BaseModel):
    message: str
    backup_snapshot_id: int


class MutationEnvelope(BaseModel):
    mutation_id: str
    op: str
    resource: str
    payload: dict
    client_timestamp: Optional[datetime] = None
    base_version: Optional[str] = None


class SyncReplayRequest(BaseModel):
    mutations: list[MutationEnvelope]


class MutationReceipt(BaseModel):
    mutation_id: str
    status: str
    server_timestamp: datetime
    message: str


class SyncReplayResponse(BaseModel):
    receipts: list[MutationReceipt]


class SyncActivityOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    mutation_id: str
    resource: str
    action: str
    conflict_fields: list[str]
    resolution: str
    details: str
    created_at: datetime


class SettingChangeRequest(BaseModel):
    key: str
    new_value: Any
    reason: str = "user_requested_change"


class SettingChangeResult(BaseModel):
    key: str
    old_value: Any
    new_value: Any
    risk_level: Literal["low", "critical"]
    applied: bool
    proposal_id: Optional[str] = None


class RuntimeSettingOut(BaseModel):
    key: str
    value: Any
    default_value: Any
    is_default: bool
    updated_by: str
    updated_at: Optional[datetime] = None


class SettingsValidateOut(BaseModel):
    key: str
    normalized_value: Any
    old_value: Any
    risk_level: Literal["low", "critical"]


class SettingsDiffOut(BaseModel):
    key: str
    current_value: Any
    default_value: Any
    changed: bool


class SettingsHistoryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    key: str
    old_value_json: str
    new_value_json: str
    actor: str
    reason: str
    risk_level: str
    created_at: datetime


class SettingsProposalOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    key: str
    old_value_json: str
    new_value_json: str
    actor: str
    reason: str
    risk_level: str
    status: str
    created_at: datetime
    resolved_at: Optional[datetime]
    resolved_by: Optional[str]


class SettingsResetRequest(BaseModel):
    reason: str = "reset_to_default"


class SettingsRollbackRequest(BaseModel):
    key: str
    reason: str = "rollback"
