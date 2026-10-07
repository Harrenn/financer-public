from __future__ import annotations

from datetime import date as dt_date
from datetime import datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class APIMessage(BaseModel):
    message: str


class TaxonomyValueCreate(BaseModel):
    domain: str
    label: str
    key: str | None = None
    semantic_role: str | None = None
    is_system: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)


class TaxonomyAliasCreate(BaseModel):
    domain: str
    alias: str
    taxonomy_value_id: int
    confidence: float = Field(default=1.0, ge=0, le=1)
    created_by: str = "agent"


class TaxonomyResolveRequest(BaseModel):
    domain: str
    term: str | int
    semantic_role: str | None = None
    autocreate: bool = True


class TaxonomyValueOut(BaseModel):
    id: int
    domain: str
    key: str
    label: str
    semantic_role: str | None = None
    is_system: bool
    active: bool
    metadata: dict[str, Any] = Field(default_factory=dict)


class TaxonomyAliasOut(BaseModel):
    id: int
    domain: str
    alias: str
    taxonomy_value_id: int
    confidence: float
    created_by: str
    created_at: datetime


class TaxonomyResolveOut(BaseModel):
    resolved: bool
    confidence: float
    created: bool = False
    needs_follow_up: bool = False
    reason: str = ""
    value: TaxonomyValueOut | None = None
    candidates: list[TaxonomyValueOut] = Field(default_factory=list)


class AccountCreate(BaseModel):
    name: str
    account_type: str | int
    is_liquid: bool = True
    opening_balance: Decimal = Decimal("0.00")


class AccountOut(BaseModel):
    id: int
    name: str
    account_type: TaxonomyValueOut
    is_liquid: bool
    opening_balance: Decimal
    currency: str
    current_balance: Decimal


class IncomeSourceCreate(BaseModel):
    name: str
    source_type: str | int
    payout_method: str | int | None = None
    is_salary: bool = False
    default_account_id: int | None = None
    monthly_net_amount: Decimal | None = None
    pay_schedule_kind: str | int | None = None
    pay_days: list[int] = Field(default_factory=list)
    active: bool = True


class IncomeSourceOut(BaseModel):
    id: int
    name: str
    source_type: TaxonomyValueOut
    payout_method: TaxonomyValueOut | None = None
    is_salary: bool
    default_account_id: int | None = None
    monthly_net_amount: Decimal | None = None
    pay_schedule_kind: TaxonomyValueOut | None = None
    pay_days: list[int] = Field(default_factory=list)
    active: bool
    created_at: datetime


class TransactionCreate(BaseModel):
    date: dt_date
    kind: str | int
    amount: Decimal = Field(gt=0)
    from_account_id: int | None = None
    to_account_id: int | None = None
    income_source_id: int | None = None
    category_id: int | None = None
    memo: str = ""


class TransactionOut(BaseModel):
    id: int
    date: dt_date
    kind: TaxonomyValueOut
    amount: Decimal
    from_account_id: int | None
    to_account_id: int | None
    income_source_id: int | None
    category_id: int | None
    memo: str
    created_at: datetime


class SalaryPaymentRequest(BaseModel):
    income_source_id: int
    date: dt_date | None = None
    amount: Decimal | None = None
    to_account_id: int | None = None
    memo: str = "salary payment"


class StatsOut(BaseModel):
    period: str
    start_date: dt_date
    end_date: dt_date
    transaction_count: int
    totals: dict[str, Decimal]
    breakdowns: dict[str, list[dict[str, Decimal | str]]]


class DashboardOut(BaseModel):
    cash_on_hand: Decimal
    savings_funds: Decimal
    liabilities_total: Decimal
    net_worth: Decimal


class AgentRunStepOut(BaseModel):
    id: int
    step_order: int
    agent_name: str
    status: str
    error: str | None = None
    input_json: dict[str, Any] = Field(default_factory=dict)
    output_json: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime


class AgentRunOut(BaseModel):
    id: int
    user_input: str
    assistant_output: str
    status: str
    created_at: datetime
    steps: list[AgentRunStepOut] = Field(default_factory=list)


class MultiAgentAction(BaseModel):
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)


class MultiAgentPlan(BaseModel):
    assistant_message: str = ""
    needs_follow_up: bool = False
    follow_up_question: str | None = None
    actions: list[MultiAgentAction] = Field(default_factory=list)


class MultiAgentExecutionResult(BaseModel):
    assistant_message: str
    tool_outputs: list[dict[str, Any]] = Field(default_factory=list)
    run_id: int | None = None
