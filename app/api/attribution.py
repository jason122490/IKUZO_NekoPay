"""Member-facing auto-attribution: find same-amount unattributed real txns and
attribute the chosen one to oneself."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db import get_session
from app.models.enums import AttributionStatus, RealKind
from app.models.real import RealTransaction
from app.models.user import Member
from app.schemas import (
    CandidatesOut,
    LedgerEntryOut,
    MatchIn,
    RealTxnOut,
    SelfAttributeIn,
)
from app.security import get_current_member, verify_csrf
from app.services import attribution_service, config_service

router = APIRouter(prefix="/api/attribution", tags=["attribution"])
settings = get_settings()


@router.post("/match", response_model=CandidatesOut, dependencies=[Depends(verify_csrf)])
async def match(
    payload: MatchIn,
    request: Request,
    _member: Member = Depends(get_current_member),
    session: AsyncSession = Depends(get_session),
) -> CandidatesOut:
    # Best-effort on-demand sync so the member's just-made real txn shows up.
    synced = False
    sync_manager = getattr(request.app.state, "sync_manager", None)
    if sync_manager is not None:
        # always pull fresh pay history so the just-made real txn shows up
        run = await sync_manager.sync_now_safe(include_snapshot=False)
        synced = run is not None and run.status == "ok"

    # signed value to match: topup is +points, pay is -points
    target = payload.points if payload.kind == RealKind.TOPUP.value else -payload.points
    rows = (
        await session.execute(
            select(RealTransaction)
            .where(
                RealTransaction.kind == payload.kind,
                RealTransaction.attribution_status
                == AttributionStatus.UNATTRIBUTED.value,
                RealTransaction.value == target,
            )
            .order_by(RealTransaction.occurred_at.desc())
            .limit(10)
        )
    ).scalars()
    return CandidatesOut(
        candidates=[RealTxnOut.model_validate(r) for r in rows], synced=synced
    )


@router.post(
    "/self/{real_txn_id}",
    response_model=LedgerEntryOut,
    dependencies=[Depends(verify_csrf)],
)
async def self_attribute(
    real_txn_id: int,
    payload: SelfAttributeIn,
    member: Member = Depends(get_current_member),
    session: AsyncSession = Depends(get_session),
) -> LedgerEntryOut:
    rate = await config_service.get_rate(session, settings.default_rate_nt_per_point)
    # the member reports the NT$ they paid for this top-up (pay txns ignore it)
    money_nt = payload.money_nt
    entry = await attribution_service.attribute(
        session,
        real_txn_id=real_txn_id,
        member_id=member.id,
        actor_id=member.id,
        rate=rate,
        money_nt=money_nt,
    )
    return LedgerEntryOut.model_validate(entry)
