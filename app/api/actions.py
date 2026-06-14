"""The core member actions: 儲值 (top-up), 投幣 (play), 轉點 (transfer)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db import get_session
from app.models.real import AccountSnapshot
from app.models.user import Member
from app.schemas import LedgerEntryOut, PlayIn, TopUpIn, TransferIn, TransferOut
from app.security import get_current_member, verify_csrf
from app.services import config_service, ledger_service
from app.util.time import local_to_utc
from app.vip import bonus_pct_for, topup_breakdown

router = APIRouter(prefix="/api", tags=["actions"], dependencies=[Depends(verify_csrf)])
settings = get_settings()


def _is_admin(m: Member) -> bool:
    return m.role == "admin"


async def _shared_card_bonus_pct(session: AsyncSession) -> int:
    """Top-up bonus % from the shared card's current VIP tier (latest snapshot)."""
    snap = (await session.execute(
        select(AccountSnapshot).order_by(AccountSnapshot.captured_at.desc()).limit(1)
    )).scalar_one_or_none()
    return bonus_pct_for(snap.vip_name) if snap else 0


@router.post("/topups", response_model=LedgerEntryOut)
async def create_topup(
    payload: TopUpIn,
    viewer: Member = Depends(get_current_member),
    session: AsyncSession = Depends(get_session),
) -> LedgerEntryOut:
    if payload.member_id != viewer.id and not _is_admin(viewer):
        raise HTTPException(status_code=403, detail="cannot top up for another member")
    # points are derived from the money paid: floor(money/rate) + VIP bonus
    rate = await config_service.get_rate(session, settings.default_rate_nt_per_point)
    bonus_pct = await _shared_card_bonus_pct(session)
    breakdown = topup_breakdown(payload.money_nt, rate, bonus_pct)
    if breakdown["total"] <= 0:
        raise HTTPException(status_code=400, detail="金額太少，不足 1 點")
    note = payload.note or ""
    if breakdown["bonus"]:
        note = (note + f"（含 VIP 加贈 {breakdown['bonus']} 點）").strip()
    entry = await ledger_service.record_topup(
        session,
        member_id=payload.member_id,
        points=breakdown["total"],
        money_nt=payload.money_nt,
        created_by=viewer.id,
        note=note or None,
        idempotency_key=payload.idempotency_key,
    )
    return LedgerEntryOut.model_validate(entry)


@router.post("/plays", response_model=LedgerEntryOut)
async def create_play(
    payload: PlayIn,
    viewer: Member = Depends(get_current_member),
    session: AsyncSession = Depends(get_session),
) -> LedgerEntryOut:
    if payload.member_id != viewer.id and not _is_admin(viewer):
        raise HTTPException(status_code=403, detail="cannot play for another member")
    # only admins may force a negative balance (deliberate debt)
    allow_negative = payload.allow_negative and _is_admin(viewer)
    created_at = (
        local_to_utc(payload.occurred_at, settings.app_timezone)
        if payload.occurred_at is not None else None
    )
    entry = await ledger_service.record_play(
        session,
        member_id=payload.member_id,
        points=payload.points,
        created_by=viewer.id,
        note=payload.note,
        idempotency_key=payload.idempotency_key,
        allow_negative=allow_negative,
        created_at=created_at,
    )
    return LedgerEntryOut.model_validate(entry)


@router.post("/transfers", response_model=TransferOut)
async def create_transfer(
    payload: TransferIn,
    viewer: Member = Depends(get_current_member),
    session: AsyncSession = Depends(get_session),
) -> TransferOut:
    if payload.from_member_id != viewer.id and not _is_admin(viewer):
        raise HTTPException(
            status_code=403, detail="can only transfer from your own account"
        )
    created_at = (
        local_to_utc(payload.occurred_at, settings.app_timezone)
        if payload.occurred_at is not None else None
    )
    out_row, in_row = await ledger_service.transfer(
        session,
        from_member_id=payload.from_member_id,
        to_member_id=payload.to_member_id,
        points=payload.points,
        created_by=viewer.id,
        note=payload.note,
        idempotency_key=payload.idempotency_key,
        created_at=created_at,
    )
    return TransferOut(
        transfer_group_id=out_row.transfer_group_id,
        out_entry=LedgerEntryOut.model_validate(out_row),
        in_entry=LedgerEntryOut.model_validate(in_row),
    )
