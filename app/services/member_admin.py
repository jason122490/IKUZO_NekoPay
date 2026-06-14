"""Admin-only member account lifecycle: create, edit, role, enable/disable, reset password.

Self-lockout protection: an admin cannot disable their own account nor change
their own role (so they can't accidentally lock themselves out). Disabling or
resetting a password revokes that member's active sessions immediately.
"""
from __future__ import annotations

from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit import AppSetting, AuditLog
from app.models.auth import UserSession
from app.models.enums import AttributionStatus, Role
from app.models.ledger import AttributionClaim, LedgerEntry
from app.models.real import AccountSnapshot, RealTransaction, SyncRun, SyncState
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


async def _email_taken(
    session: AsyncSession, email: str, exclude_id: int | None = None
) -> bool:
    stmt = select(Member.id).where(func.lower(Member.email) == email.strip().lower())
    if exclude_id is not None:
        stmt = stmt.where(Member.id != exclude_id)
    return (await session.execute(stmt.limit(1))).scalar_one_or_none() is not None


async def _name_taken(
    session: AsyncSession, name: str, exclude_id: int | None = None
) -> bool:
    stmt = select(Member.id).where(
        func.lower(Member.display_name) == name.strip().lower()
    )
    if exclude_id is not None:
        stmt = stmt.where(Member.id != exclude_id)
    return (await session.execute(stmt.limit(1))).scalar_one_or_none() is not None


async def create_member(
    session: AsyncSession,
    *,
    actor_id: int,
    email: str,
    display_name: str,
    password: str,
    role: str = "member",
) -> Member:
    name = display_name.strip()
    if not name:
        raise ValidationError("暱稱不可空白")
    if await _email_taken(session, email):
        raise ConflictError("Email 已被使用")
    if await _name_taken(session, name):
        raise ConflictError("暱稱已被使用")
    member = Member(
        email=email.lower(),
        display_name=name,
        password_hash=hash_password(password),  # validates length
        role=_norm_role(role),
    )
    session.add(member)
    try:
        await session.flush()
    except IntegrityError:
        await session.rollback()
        raise ConflictError("Email 或暱稱已被使用")
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
        name = display_name.strip()
        if not name:
            raise ValidationError("暱稱不可空白")
        if await _name_taken(session, name, exclude_id=member_id):
            raise ConflictError("暱稱已被使用")
        member.display_name = name
        changes["display_name"] = name
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


async def count_references(session: AsyncSession, member_id: int) -> int:
    """Count financial/decision records that reference this member.

    These (ledger ownership/authorship, attributions, claims) make a hard delete
    unsafe; user_sessions and audit_log are handled separately and don't count.
    """
    total = 0
    for stmt in (
        select(func.count()).select_from(LedgerEntry).where(
            or_(LedgerEntry.member_id == member_id, LedgerEntry.created_by == member_id)
        ),
        select(func.count()).select_from(RealTransaction).where(
            or_(
                RealTransaction.attributed_member_id == member_id,
                RealTransaction.attributed_by == member_id,
            )
        ),
        select(func.count()).select_from(AttributionClaim).where(
            or_(
                AttributionClaim.member_id == member_id,
                AttributionClaim.resolved_by == member_id,
            )
        ),
    ):
        total += int((await session.execute(stmt)).scalar_one())
    return total


async def _force_purge(session: AsyncSession, member_id: int, actor_id: int) -> None:
    """Remove all records tied to a member so the member row can be deleted.

    Side effects (intentional, for integrity):
      * transfers the member was part of are removed in full (both sides), which
        reverses those transfers for the counterparties;
      * real transactions attributed to the member return to 'unattributed';
      * records the member created for OTHERS keep, with authorship reassigned to
        the admin performing the deletion.
    """
    # 1) delete full transfer groups the member participated in (both sides)
    group_ids = [
        g for (g,) in (await session.execute(
            select(LedgerEntry.transfer_group_id)
            .where(
                LedgerEntry.member_id == member_id,
                LedgerEntry.transfer_group_id.is_not(None),
            )
            .distinct()
        )).all()
    ]
    if group_ids:
        await session.execute(
            delete(LedgerEntry).where(LedgerEntry.transfer_group_id.in_(group_ids))
        )
    # 2) free real transactions attributed to this member
    await session.execute(
        update(RealTransaction)
        .where(RealTransaction.attributed_member_id == member_id)
        .values(
            attribution_status=AttributionStatus.UNATTRIBUTED.value,
            attributed_member_id=None, attributed_by=None,
            attributed_at=None, ledger_entry_id=None,
        )
    )
    # 3) delete the member's remaining ledger entries (topup/play/adjustment)
    await session.execute(
        delete(LedgerEntry).where(LedgerEntry.member_id == member_id)
    )
    # 4) reassign authorship of entries the member created for OTHERS
    await session.execute(
        update(LedgerEntry).where(LedgerEntry.created_by == member_id)
        .values(created_by=actor_id)
    )
    # 5) clear 'attributed_by' the member set on others' real txns
    await session.execute(
        update(RealTransaction).where(RealTransaction.attributed_by == member_id)
        .values(attributed_by=None)
    )
    # 6) claims: delete the member's own, null them as a resolver elsewhere
    await session.execute(
        delete(AttributionClaim).where(AttributionClaim.member_id == member_id)
    )
    await session.execute(
        update(AttributionClaim).where(AttributionClaim.resolved_by == member_id)
        .values(resolved_by=None)
    )


async def delete_member(
    session: AsyncSession, *, actor_id: int, member_id: int, force: bool = False
) -> None:
    """Delete a member.

    Without ``force``: refuses if the member has any financial/decision records
    (disable instead). With ``force``: purges those records too (see _force_purge).
    """
    if member_id == actor_id:
        raise ValidationError("cannot delete your own account")
    member = await _require(session, member_id)

    refs = await count_references(session, member_id)
    if refs and not force:
        raise ConflictError(
            f"此帳號有 {refs} 筆關聯紀錄；請改用停用，或選擇強制刪除（連同紀錄）"
        )
    if force and refs:
        await _force_purge(session, member_id, actor_id)

    # common cleanup: drop sessions, keep audit history but null the actor FK
    await session.execute(
        delete(UserSession).where(UserSession.member_id == member_id)
    )
    await session.execute(
        update(AuditLog).where(AuditLog.actor_id == member_id).values(actor_id=None)
    )
    await audit_service.record(
        session,
        actor_id=actor_id,
        action=("member.force_delete" if force and refs else "member.delete"),
        target_type="member", target_id=member_id,
        detail={"email": member.email, "display_name": member.display_name,
                "purged_refs": refs},
    )
    await session.delete(member)
    await session.commit()


async def reset_database(session: AsyncSession, *, keep_member_id: int) -> None:
    """Wipe all data, keeping only the acting admin's account (and their session).

    Removes every member except the acting admin, plus all ledger entries, real
    transactions, snapshots, sync runs/state, claims, audit log, and app
    settings. Irreversible.
    """
    await session.execute(delete(LedgerEntry))
    await session.execute(delete(AttributionClaim))
    await session.execute(delete(RealTransaction))
    await session.execute(delete(AccountSnapshot))
    await session.execute(delete(SyncRun))
    await session.execute(delete(SyncState))
    await session.execute(delete(AuditLog))
    await session.execute(delete(AppSetting))
    await session.execute(delete(Member).where(Member.id != keep_member_id))
    await session.execute(
        delete(UserSession).where(UserSession.member_id != keep_member_id)
    )
    await audit_service.record(
        session, actor_id=keep_member_id, action="db.reset", target_type="db"
    )
    await session.commit()


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
