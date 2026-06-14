"""Admin endpoints: sync, attribution, claims, adjustments, reconciliation."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db import get_session
from app.models.real import RealTransaction, SyncRun
from app.models.user import Member
from app.schemas import (
    AdjustmentIn,
    AttributeIn,
    IgnoreIn,
    LedgerEntryOut,
    MessageOut,
    RateIn,
    RateOut,
    ReconciliationOut,
    RealTxnOut,
    SyncRunOut,
)
from app.security import require_admin, verify_csrf
from app.services import attribution_service, config_service, ledger_service
from app.services.reconciliation import reconcile_report

router = APIRouter(
    prefix="/api/admin",
    tags=["admin"],
    dependencies=[Depends(require_admin)],
)
settings = get_settings()


async def _rate(session: AsyncSession):
    return await config_service.get_rate(session, settings.default_rate_nt_per_point)


@router.post("/sync/run-now", response_model=SyncRunOut, dependencies=[Depends(verify_csrf)])
async def sync_now(request: Request) -> SyncRunOut:
    run = await request.app.state.sync_manager.run_once()
    return SyncRunOut.model_validate(run)


@router.get("/sync-runs", response_model=list[SyncRunOut])
async def sync_runs(
    session: AsyncSession = Depends(get_session), limit: int = 20
) -> list[SyncRunOut]:
    rows = (
        await session.execute(
            select(SyncRun).order_by(SyncRun.started_at.desc()).limit(limit)
        )
    ).scalars()
    return [SyncRunOut.model_validate(r) for r in rows]


@router.get("/real-transactions", response_model=list[RealTxnOut])
async def real_transactions(
    session: AsyncSession = Depends(get_session),
    status: str | None = None,
    limit: int = 100,
) -> list[RealTxnOut]:
    stmt = select(RealTransaction).order_by(RealTransaction.occurred_at.desc())
    if status:
        stmt = stmt.where(RealTransaction.attribution_status == status)
    rows = (await session.execute(stmt.limit(min(limit, 500)))).scalars()
    return [RealTxnOut.model_validate(r) for r in rows]


@router.post(
    "/real-transactions/{txn_id}/attribute",
    response_model=LedgerEntryOut,
    dependencies=[Depends(verify_csrf)],
)
async def attribute_txn(
    txn_id: int,
    payload: AttributeIn,
    admin: Member = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> LedgerEntryOut:
    entry = await attribution_service.attribute(
        session,
        real_txn_id=txn_id,
        member_id=payload.member_id,
        actor_id=admin.id,
        rate=await _rate(session),
    )
    return LedgerEntryOut.model_validate(entry)


@router.post(
    "/real-transactions/{txn_id}/ignore",
    response_model=MessageOut,
    dependencies=[Depends(verify_csrf)],
)
async def ignore_txn(
    txn_id: int,
    payload: IgnoreIn,
    admin: Member = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> MessageOut:
    await attribution_service.ignore_real_txn(
        session, real_txn_id=txn_id, actor_id=admin.id, reason=payload.reason
    )
    return MessageOut(detail="ignored")


@router.post(
    "/claims/{claim_id}/approve",
    response_model=LedgerEntryOut,
    dependencies=[Depends(verify_csrf)],
)
async def approve_claim(
    claim_id: int,
    admin: Member = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> LedgerEntryOut:
    entry = await attribution_service.approve_claim(
        session, claim_id=claim_id, actor_id=admin.id, rate=await _rate(session)
    )
    return LedgerEntryOut.model_validate(entry)


@router.post(
    "/claims/{claim_id}/reject",
    response_model=MessageOut,
    dependencies=[Depends(verify_csrf)],
)
async def reject_claim(
    claim_id: int,
    admin: Member = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> MessageOut:
    await attribution_service.reject_claim(
        session, claim_id=claim_id, actor_id=admin.id
    )
    return MessageOut(detail="rejected")


@router.post(
    "/adjustments", response_model=LedgerEntryOut, dependencies=[Depends(verify_csrf)]
)
async def adjustment(
    payload: AdjustmentIn,
    admin: Member = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> LedgerEntryOut:
    entry = await ledger_service.record_adjustment(
        session,
        member_id=payload.member_id,
        points_delta=payload.points_delta,
        created_by=admin.id,
        reason=payload.reason,
    )
    return LedgerEntryOut.model_validate(entry)


@router.get("/rate", response_model=RateOut)
async def get_rate(session: AsyncSession = Depends(get_session)) -> RateOut:
    return RateOut(rate=await _rate(session))


@router.post("/rate", response_model=RateOut, dependencies=[Depends(verify_csrf)])
async def set_rate(
    payload: RateIn,
    admin: Member = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> RateOut:
    rate = await config_service.set_rate(session, payload.rate)
    return RateOut(rate=rate)


@router.get("/reconciliation", response_model=ReconciliationOut)
async def reconciliation(
    session: AsyncSession = Depends(get_session),
) -> ReconciliationOut:
    rep = await reconcile_report(session)
    return ReconciliationOut(
        internal_total=rep.internal_total,
        pooled_balance=rep.pooled_balance,
        drift=rep.drift,
        snapshot_age_sec=rep.snapshot_age_sec,
        unattributed_count=rep.unattributed_count,
        unattributed_value=rep.unattributed_value,
        manual_entry_count=rep.manual_entry_count,
    )
