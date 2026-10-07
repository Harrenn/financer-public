from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from .. import db as db_module
from . import finance
from .schemas import (
    APIMessage,
    AccountCreate,
    AccountOut,
    AgentRunOut,
    DashboardOut,
    IncomeSourceCreate,
    IncomeSourceOut,
    SalaryPaymentRequest,
    StatsOut,
    TaxonomyAliasCreate,
    TaxonomyAliasOut,
    TaxonomyResolveOut,
    TaxonomyResolveRequest,
    TaxonomyValueCreate,
    TaxonomyValueOut,
    TransactionCreate,
    TransactionOut,
)
from .seed import seed_defaults_v2
from .taxonomy import (
    create_taxonomy_alias,
    create_taxonomy_value,
    list_taxonomy_values,
    resolve_taxonomy_term,
    taxonomy_value_to_dict,
)

router = APIRouter(prefix="/api", tags=["api-v2"])


def get_db() -> Session:
    db = db_module.SessionLocal()
    try:
        yield db
    finally:
        db.close()


@router.get("/health", response_model=APIMessage)
def health() -> APIMessage:
    return APIMessage(message="Financer V2 API is healthy")


@router.post("/setup/init", response_model=APIMessage)
def setup_init(db: Session = Depends(get_db)) -> APIMessage:
    seed_defaults_v2(db)
    return APIMessage(message="Financer V2 database initialized")


@router.get("/taxonomies", response_model=list[TaxonomyValueOut])
def get_taxonomies(domain: str | None = None, db: Session = Depends(get_db)) -> list[TaxonomyValueOut]:
    values = list_taxonomy_values(db, domain)
    return [TaxonomyValueOut(**taxonomy_value_to_dict(item)) for item in values]


@router.post("/taxonomies", response_model=TaxonomyValueOut)
def post_taxonomy(payload: TaxonomyValueCreate, db: Session = Depends(get_db)) -> TaxonomyValueOut:
    try:
        row = create_taxonomy_value(
            db,
            domain=payload.domain,
            label=payload.label,
            key=payload.key,
            semantic_role=payload.semantic_role,
            is_system=payload.is_system,
            metadata=payload.metadata,
        )
        db.commit()
        db.refresh(row)
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return TaxonomyValueOut(**taxonomy_value_to_dict(row))


@router.post("/taxonomies/resolve", response_model=TaxonomyResolveOut)
def post_taxonomy_resolve(payload: TaxonomyResolveRequest, db: Session = Depends(get_db)) -> TaxonomyResolveOut:
    result = resolve_taxonomy_term(
        db,
        domain=payload.domain,
        term=payload.term,
        semantic_role=payload.semantic_role,
        autocreate=payload.autocreate,
        created_by="api",
    )
    db.commit()

    return TaxonomyResolveOut(
        resolved=result.resolved,
        confidence=result.confidence,
        created=result.created,
        needs_follow_up=result.needs_follow_up,
        reason=result.reason,
        value=TaxonomyValueOut(**taxonomy_value_to_dict(result.value)) if result.value else None,
        candidates=[TaxonomyValueOut(**taxonomy_value_to_dict(item)) for item in result.candidates],
    )


@router.post("/taxonomies/aliases", response_model=TaxonomyAliasOut)
def post_taxonomy_alias(payload: TaxonomyAliasCreate, db: Session = Depends(get_db)) -> TaxonomyAliasOut:
    try:
        row = create_taxonomy_alias(
            db,
            domain=payload.domain,
            alias=payload.alias,
            taxonomy_value_id=payload.taxonomy_value_id,
            confidence=payload.confidence,
            created_by=payload.created_by,
        )
        db.commit()
        db.refresh(row)
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return TaxonomyAliasOut(
        id=row.id,
        domain=row.domain,
        alias=row.alias,
        taxonomy_value_id=row.taxonomy_value_id,
        confidence=row.confidence,
        created_by=row.created_by,
        created_at=row.created_at,
    )


@router.get("/accounts", response_model=list[AccountOut])
def get_accounts(db: Session = Depends(get_db)) -> list[AccountOut]:
    return [AccountOut(**item) for item in finance.list_accounts(db)]


@router.post("/accounts", response_model=AccountOut)
def post_account(payload: AccountCreate, db: Session = Depends(get_db)) -> AccountOut:
    try:
        account = finance.create_account(
            db,
            name=payload.name,
            account_type=payload.account_type,
            is_liquid=payload.is_liquid,
            opening_balance=payload.opening_balance,
        )
        db.commit()
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return AccountOut(**account)


@router.get("/income-sources", response_model=list[IncomeSourceOut])
def get_income_sources(db: Session = Depends(get_db)) -> list[IncomeSourceOut]:
    return [IncomeSourceOut(**item) for item in finance.list_income_sources(db)]


@router.post("/income-sources", response_model=IncomeSourceOut)
def post_income_source(payload: IncomeSourceCreate, db: Session = Depends(get_db)) -> IncomeSourceOut:
    try:
        row = finance.upsert_income_source_with_salary_profile(
            db,
            name=payload.name,
            source_type=payload.source_type,
            payout_method=payload.payout_method,
            is_salary=payload.is_salary,
            default_account_id=payload.default_account_id,
            monthly_net_amount=payload.monthly_net_amount,
            pay_schedule_kind=payload.pay_schedule_kind,
            pay_days=payload.pay_days,
            active=payload.active,
        )
        db.commit()
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return IncomeSourceOut(**row)


@router.post("/transactions", response_model=TransactionOut)
def post_transaction(payload: TransactionCreate, db: Session = Depends(get_db)) -> TransactionOut:
    try:
        row = finance.create_transaction(
            db,
            date_value=payload.date,
            kind=payload.kind,
            amount=payload.amount,
            from_account_id=payload.from_account_id,
            to_account_id=payload.to_account_id,
            income_source_id=payload.income_source_id,
            category_id=payload.category_id,
            memo=payload.memo,
        )
        db.commit()
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return TransactionOut(**row)


@router.post("/salary/payments", response_model=TransactionOut)
def post_salary_payment(payload: SalaryPaymentRequest, db: Session = Depends(get_db)) -> TransactionOut:
    try:
        row = finance.record_salary_payment_from_profile(
            db,
            income_source_id=payload.income_source_id,
            date_value=payload.date,
            amount=payload.amount,
            to_account_id=payload.to_account_id,
            memo=payload.memo,
        )
        db.commit()
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return TransactionOut(**row)


@router.get("/dashboard", response_model=DashboardOut)
def get_dashboard(db: Session = Depends(get_db)) -> DashboardOut:
    return DashboardOut(**finance.dashboard_metrics(db))


@router.get("/stats", response_model=StatsOut)
def get_stats(
    period: str = "month",
    start_date: date | None = None,
    end_date: date | None = None,
    db: Session = Depends(get_db),
) -> StatsOut:
    try:
        payload = finance.compute_stats(db, period=period, start_date=start_date, end_date=end_date)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return StatsOut(**payload)


@router.get("/agent/runs/latest", response_model=AgentRunOut | None)
def get_latest_agent_run(db: Session = Depends(get_db)) -> AgentRunOut | None:
    row = finance.latest_agent_run(db)
    if not row:
        return None
    return AgentRunOut(**row)
