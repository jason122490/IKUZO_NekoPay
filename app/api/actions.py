"""The core member actions: 儲值 (top-up), 投幣 (play), 轉點 (transfer)."""
from __future__ import annotations

from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db import get_session
from app.models.user import Member
from app.schemas import LedgerEntryOut, PlayIn, TopUpIn, TransferIn, TransferOut
from app.security import get_current_member, verify_csrf
from app.services import config_service, ledger_service

router = APIRouter(prefix="/api", tags=["actions"], dependencies=[Depends(verify_csrf)])
settings = get_settings()


def _is_admin(m: Member) -> bool:
    return m.role == "admin"


@router.post("/topups", response_model=LedgerEntryOut)
async def create_topup(
    payload: TopUpIn,
    viewer: Member = Depends(get_current_member),
    session: AsyncSession = Depends(get_session),
) -> LedgerEntryOut:
    if payload.member_id != viewer.id and not _is_admin(viewer):
        raise HTTPException(status_code=403, detail="cannot top up for another member")
    # NT$ comes from the admin-set rate; only admins may override it explicitly.
    rate = await config_service.get_rate(session, settings.default_rate_nt_per_point)
    if _is_admin(viewer) and payload.money_nt is not None:
        money = payload.money_nt
    else:
        money = Decimal(payload.points) * rate
    entry = await ledger_service.record_topup(
        session,
        member_id=payload.member_id,
        points=payload.points,
        money_nt=money,
        created_by=viewer.id,
        note=payload.note,
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
    entry = await ledger_service.record_play(
        session,
        member_id=payload.member_id,
        points=payload.points,
        created_by=viewer.id,
        note=payload.note,
        idempotency_key=payload.idempotency_key,
        allow_negative=allow_negative,
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
    out_row, in_row = await ledger_service.transfer(
        session,
        from_member_id=payload.from_member_id,
        to_member_id=payload.to_member_id,
        points=payload.points,
        created_by=viewer.id,
        note=payload.note,
        idempotency_key=payload.idempotency_key,
    )
    return TransferOut(
        transfer_group_id=out_row.transfer_group_id,
        out_entry=LedgerEntryOut.model_validate(out_row),
        in_entry=LedgerEntryOut.model_validate(in_row),
    )
