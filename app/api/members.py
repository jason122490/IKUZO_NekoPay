"""Member management + per-member views (創建帳號 + balances/ledger)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.models.ledger import LedgerEntry
from app.models.user import Member
from app.schemas import BalanceOut, LedgerEntryOut, MemberCreateIn, MemberOut
from app.security import get_current_member, require_admin, verify_csrf
from app.services import ledger_service
from app.services.auth_service import hash_password

router = APIRouter(prefix="/api/members", tags=["members"])


def _ensure_self_or_admin(viewer: Member, member_id: int) -> None:
    if viewer.role != "admin" and viewer.id != member_id:
        raise HTTPException(status_code=403, detail="forbidden")


@router.post(
    "", response_model=MemberOut, dependencies=[Depends(verify_csrf)]
)
async def create_member(
    payload: MemberCreateIn,
    _admin: Member = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> MemberOut:
    member = Member(
        email=payload.email.lower(),
        display_name=payload.display_name,
        password_hash=hash_password(payload.password),
        role=("admin" if payload.role == "admin" else "member"),
    )
    session.add(member)
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        raise HTTPException(status_code=409, detail="email already registered")
    await session.refresh(member)
    return MemberOut.model_validate(member)


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
