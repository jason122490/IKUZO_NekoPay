"""Member management + per-member views (創建帳號 + balances/ledger)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.models.ledger import LedgerEntry
from app.models.user import Member
from app.schemas import (
    BalanceOut,
    LedgerEntryOut,
    MemberCreateIn,
    MemberOut,
    MemberUpdateIn,
    MessageOut,
    ResetPasswordIn,
    StatusIn,
)
from app.security import get_current_member, require_admin, verify_csrf
from app.services import ledger_service, member_admin

router = APIRouter(prefix="/api/members", tags=["members"])


def _ensure_self_or_admin(viewer: Member, member_id: int) -> None:
    if viewer.role != "admin" and viewer.id != member_id:
        raise HTTPException(status_code=403, detail="forbidden")


@router.post("", response_model=MemberOut, dependencies=[Depends(verify_csrf)])
async def create_member(
    payload: MemberCreateIn,
    admin: Member = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> MemberOut:
    member = await member_admin.create_member(
        session,
        actor_id=admin.id,
        username=payload.username,
        display_name=payload.display_name,
        password=payload.password,
        role=payload.role,
    )
    return MemberOut.model_validate(member)


@router.post(
    "/{member_id}/update", response_model=MemberOut, dependencies=[Depends(verify_csrf)]
)
async def update_member(
    member_id: int,
    payload: MemberUpdateIn,
    admin: Member = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> MemberOut:
    member = await member_admin.update_member(
        session,
        actor_id=admin.id,
        member_id=member_id,
        display_name=payload.display_name,
        role=payload.role,
    )
    return MemberOut.model_validate(member)


@router.post(
    "/{member_id}/status", response_model=MemberOut, dependencies=[Depends(verify_csrf)]
)
async def set_member_status(
    member_id: int,
    payload: StatusIn,
    admin: Member = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> MemberOut:
    member = await member_admin.set_active(
        session, actor_id=admin.id, member_id=member_id, is_active=payload.is_active
    )
    return MemberOut.model_validate(member)


@router.post(
    "/{member_id}/reset-password",
    response_model=MessageOut,
    dependencies=[Depends(verify_csrf)],
)
async def reset_member_password(
    member_id: int,
    payload: ResetPasswordIn,
    admin: Member = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> MessageOut:
    await member_admin.reset_password(
        session, actor_id=admin.id, member_id=member_id, new_password=payload.new_password
    )
    return MessageOut(detail="password reset")


@router.delete(
    "/{member_id}", response_model=MessageOut, dependencies=[Depends(verify_csrf)]
)
async def delete_member(
    member_id: int,
    force: bool = False,
    admin: Member = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> MessageOut:
    await member_admin.delete_member(
        session, actor_id=admin.id, member_id=member_id, force=force
    )
    return MessageOut(detail="deleted")


@router.get("", response_model=list[MemberOut])
async def list_members(
    _viewer: Member = Depends(get_current_member),
    session: AsyncSession = Depends(get_session),
) -> list[MemberOut]:
    rows = (
        await session.execute(select(Member).order_by(Member.display_name))
    ).scalars()
    return [MemberOut.model_validate(m) for m in rows]


@router.get("/{member_id}/balance", response_model=BalanceOut)
async def member_balance(
    member_id: int,
    viewer: Member = Depends(get_current_member),
    session: AsyncSession = Depends(get_session),
) -> BalanceOut:
    _ensure_self_or_admin(viewer, member_id)
    member = await session.get(Member, member_id)
    if member is None:
        raise HTTPException(status_code=404, detail="member not found")
    return BalanceOut(
        member_id=member_id,
        display_name=member.display_name,
        points_balance=await ledger_service.get_balance(session, member_id),
        money_contributed=await ledger_service.get_money_contributed(
            session, member_id
        ),
    )


@router.get("/{member_id}/ledger", response_model=list[LedgerEntryOut])
async def member_ledger(
    member_id: int,
    viewer: Member = Depends(get_current_member),
    session: AsyncSession = Depends(get_session),
    limit: int = 100,
) -> list[LedgerEntryOut]:
    _ensure_self_or_admin(viewer, member_id)
    rows = (
        await session.execute(
            select(LedgerEntry)
            .where(LedgerEntry.member_id == member_id)
            .order_by(LedgerEntry.created_at.desc())
            .limit(min(limit, 500))
        )
    ).scalars()
    return [LedgerEntryOut.model_validate(e) for e in rows]
