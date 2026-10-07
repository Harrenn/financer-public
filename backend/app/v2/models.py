from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class V2Base(DeclarativeBase):
    pass



def now_utc() -> datetime:
    return datetime.utcnow()


class TaxonomyValue(V2Base):
    __tablename__ = "v2_taxonomy_values"
    __table_args__ = (UniqueConstraint("domain", "key", name="uq_v2_taxonomy_domain_key"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    domain: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    key: Mapped[str] = mapped_column(String(120), nullable=False)
    label: Mapped[str] = mapped_column(String(160), nullable=False)
    semantic_role: Mapped[str | None] = mapped_column(String(80), nullable=True)
    is_system: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    metadata_json: Mapped[str] = mapped_column(Text, default="{}", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_utc, nullable=False)


class TaxonomyAlias(V2Base):
    __tablename__ = "v2_taxonomy_aliases"
    __table_args__ = (UniqueConstraint("domain", "alias", name="uq_v2_taxonomy_domain_alias"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    domain: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    alias: Mapped[str] = mapped_column(String(160), nullable=False)
    taxonomy_value_id: Mapped[int] = mapped_column(ForeignKey("v2_taxonomy_values.id"), nullable=False, index=True)
    confidence: Mapped[float] = mapped_column(Float, default=1.0, nullable=False)
    created_by: Mapped[str] = mapped_column(String(80), default="system", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_utc, nullable=False)

    taxonomy_value: Mapped[TaxonomyValue] = relationship()


class Account(V2Base):
    __tablename__ = "v2_accounts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True, nullable=False)
    account_type_id: Mapped[int] = mapped_column(ForeignKey("v2_taxonomy_values.id"), nullable=False)
    is_liquid: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    opening_balance: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=Decimal("0.00"), nullable=False)
    currency: Mapped[str] = mapped_column(String(8), default="PHP", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_utc, nullable=False)

    account_type: Mapped[TaxonomyValue] = relationship()


class IncomeSource(V2Base):
    __tablename__ = "v2_income_sources"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True, nullable=False)
    source_type_id: Mapped[int] = mapped_column(ForeignKey("v2_taxonomy_values.id"), nullable=False)
    payout_method_id: Mapped[int | None] = mapped_column(ForeignKey("v2_taxonomy_values.id"), nullable=True)
    is_salary: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    default_account_id: Mapped[int | None] = mapped_column(ForeignKey("v2_accounts.id"), nullable=True)
    monthly_net_amount: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    pay_schedule_kind_id: Mapped[int | None] = mapped_column(ForeignKey("v2_taxonomy_values.id"), nullable=True)
    pay_days_json: Mapped[str] = mapped_column(Text, default="[]", nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_utc, nullable=False)

    source_type: Mapped[TaxonomyValue] = relationship(foreign_keys=[source_type_id])
    payout_method: Mapped[TaxonomyValue | None] = relationship(foreign_keys=[payout_method_id])
    pay_schedule_kind: Mapped[TaxonomyValue | None] = relationship(foreign_keys=[pay_schedule_kind_id])
    default_account: Mapped[Account | None] = relationship(foreign_keys=[default_account_id])


class Category(V2Base):
    __tablename__ = "v2_categories"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True, nullable=False)
    bucket_type_id: Mapped[int] = mapped_column(ForeignKey("v2_taxonomy_values.id"), nullable=False)
    is_essential_fixed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    bucket_type: Mapped[TaxonomyValue] = relationship()


class Transaction(V2Base):
    __tablename__ = "v2_transactions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    kind_id: Mapped[int] = mapped_column(ForeignKey("v2_taxonomy_values.id"), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    from_account_id: Mapped[int | None] = mapped_column(ForeignKey("v2_accounts.id"), nullable=True)
    to_account_id: Mapped[int | None] = mapped_column(ForeignKey("v2_accounts.id"), nullable=True)
    income_source_id: Mapped[int | None] = mapped_column(ForeignKey("v2_income_sources.id"), nullable=True)
    category_id: Mapped[int | None] = mapped_column(ForeignKey("v2_categories.id"), nullable=True)
    memo: Mapped[str] = mapped_column(Text, default="", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_utc, nullable=False)

    kind: Mapped[TaxonomyValue] = relationship(foreign_keys=[kind_id])
    from_account: Mapped[Account | None] = relationship(foreign_keys=[from_account_id])
    to_account: Mapped[Account | None] = relationship(foreign_keys=[to_account_id])
    income_source: Mapped[IncomeSource | None] = relationship()
    category: Mapped[Category | None] = relationship()


class Liability(V2Base):
    __tablename__ = "v2_liabilities"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    liability_type_id: Mapped[int] = mapped_column(ForeignKey("v2_taxonomy_values.id"), nullable=False)
    account_id: Mapped[int | None] = mapped_column(ForeignKey("v2_accounts.id"), nullable=True)
    current_balance: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=Decimal("0.00"), nullable=False)
    minimum_due: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=Decimal("0.00"), nullable=False)
    due_day: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    liability_type: Mapped[TaxonomyValue] = relationship()
    account: Mapped[Account | None] = relationship()


class Loan(V2Base):
    __tablename__ = "v2_loans"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    principal: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    rate_apr: Mapped[Decimal | None] = mapped_column(Numeric(8, 4), nullable=True)
    minimum_payment: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=Decimal("0.00"), nullable=False)
    due_day: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    status_type_id: Mapped[int] = mapped_column(ForeignKey("v2_taxonomy_values.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_utc, nullable=False)

    status_type: Mapped[TaxonomyValue] = relationship()


class AgentRun(V2Base):
    __tablename__ = "v2_agent_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_input: Mapped[str] = mapped_column(Text, nullable=False)
    assistant_output: Mapped[str] = mapped_column(Text, default="", nullable=False)
    status: Mapped[str] = mapped_column(String(40), default="started", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_utc, nullable=False)


class AgentRunStep(V2Base):
    __tablename__ = "v2_agent_run_steps"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("v2_agent_runs.id"), nullable=False, index=True)
    step_order: Mapped[int] = mapped_column(Integer, nullable=False)
    agent_name: Mapped[str] = mapped_column(String(80), nullable=False)
    input_json: Mapped[str] = mapped_column(Text, default="{}", nullable=False)
    output_json: Mapped[str] = mapped_column(Text, default="{}", nullable=False)
    status: Mapped[str] = mapped_column(String(40), default="ok", nullable=False)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_utc, nullable=False)

    run: Mapped[AgentRun] = relationship()
