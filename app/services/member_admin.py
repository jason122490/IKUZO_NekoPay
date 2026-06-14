"""Admin-only member account lifecycle: create, edit, role, enable/disable, reset password.

Self-lockout protection: an admin cannot disable their own account nor change
their own role (so they can't accidentally lock themselves out). Disabling or
resetting a password revokes that member's active sessions immediately.
"""
from __future__ import annotations

from sqlalchemy import delete
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.auth import UserSession
from app.models.enums import Role
from app.models.user import Member
from app.services import audit_service
from app.services.auth_service import hash_password
from app.services.errors import ConflictError, NotFoundError, ValidationError


def _norm_role(role: str | None) -> str:
    return Role.ADMIN.value if role == Role.ADMIN.value else Role.MEMBER.value


async def _require(session: AsyncSession, member_id: int) -> Member:
    m = await session.get(Member, member_id)
    if m is None:
        raise NotFoundError(f"member {member_id} not found")
    return m


async def create_member(
    session: AsyncSession,
    *,
    actor_id: int,
    email: str,
    display_name: str,
    password: str,
    role: str = "member",
) -> Member:
    member = Member(
        email=email.lower(),
        display_name=display_name,
        password_hash=hash_password(password),  # validates length
        role=_norm_role(role),
    )
    session.add(member)
    try:
        await session.flush()
    except IntegrityError:
        await session.rollback()
        raise ConflictError("email already registered")
    await audit_service.record(
        session, actor_id=actor_id, action="member.create",
        target_type="member", target_id=member.id,
        detail={"email": member.email, "role": member.role},
    )
    await session.commit()
    await session.refresh(member)
    return member


async def update_member(
    session: AsyncSession,
    *,
    actor_id: int,
    member_id: int,
    display_name: str | None = None,
    role: str | None = None,
) -> Member:
    member = await _require(session, member_id)
    if role is not None and member_id == actor_id and _norm_role(role) != member.role:
        raise ValidationError("cannot change your own role")
    changes: dict = {}
    if display_name:
        member.display_name = display_name
        changes["display_name"] = display_name
    if role is not None:
        member.role = _norm_role(role)
        changes["role"] = member.role
    if changes:
        await audit_service.record(
            session, actor_id=actor_id, action="member.update",
            target_type="member", target_id=member_id, detail=changes,
        )
    await session.commit()
    await session.refresh(member)
    return member


async def set_active(
    session: AsyncSession, *, actor_id: int, member_id: int, is_active: bool
) -> Member:
    if member_id == actor_id and not is_active:
        raise ValidationError("cannot disable your own account")
    member = await _require(session, member_id)
    member.is_active = is_active
    if not is_active:
        await session.execute(
            delete(UserSession).where(UserSession.member_id == member_id)
        )  # force logout immediately
    await audit_service.record(
        session, actor_id=actor_id, action="member.set_active",
        target_type="member", target_id=member_id, detail={"is_active": is_active},
    )
    await session.commit()
    await session.refresh(member)
    return member


async def reset_password(
    session: AsyncSession, *, actor_id: int, member_id: int, new_password: str
) -> Member:
    member = await _require(session, member_id)
    member.password_hash = hash_password(new_password)  # validates length
    await session.execute(
        delete(UserSession).where(UserSession.member_id == member_id)
    )  # force re-login with the new password
    await audit_service.record(
        session, actor_id=actor_id, action="member.reset_password",
        target_type="member", target_id=member_id,
    )
    await session.commit()
    return member
