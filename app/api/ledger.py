"""Edit / delete individual ledger records (own within 30 min; admin anytime)."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.models.user import Member
from app.schemas import EditEntryIn, LedgerEntryOut, MessageOut
from app.security import get_current_member, verify_csrf
from app.services import ledger_edit

router = APIRouter(prefix="/api/ledger", tags=["ledger"], dependencies=[Depends(verify_csrf)])


@router.post("/{entry_id}/edit", response_model=LedgerEntryOut)
async def edit_entry(
    entry_id: int,
    payload: EditEntryIn,
    member: Member = Depends(get_current_member),
    session: AsyncSession = Depends(get_session),
) -> LedgerEntryOut:
    entry = await ledger_edit.edit_entry(
        session,
        actor=member,
        entry_id=entry_id,
        points=payload.points,
        money_nt=payload.money_nt,
        note=payload.note,
    )
    return LedgerEntryOut.model_validate(entry)


@router.delete("/{entry_id}", response_model=MessageOut)
async def delete_entry(
    entry_id: int,
    member: Member = Depends(get_current_member),
    session: AsyncSession = Depends(get_session),
) -> MessageOut:
    await ledger_edit.delete_entry(session, actor=member, entry_id=entry_id)
    return MessageOut(detail="deleted")
