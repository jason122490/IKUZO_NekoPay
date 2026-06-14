"""Edit / delete ledger records.

Policy:
  * admins may edit/delete any record;
  * members may edit/delete only their OWN records (no time limit).

Side effects handled:
  * deleting an auto-attributed entry frees its real transaction (back to
    unattributed, so it can be re-attributed);
  * deleting one side of a transfer deletes BOTH sides (conserves the total);
  * transfers cannot be edited (delete the pair and recreate instead);
  * edits/deletes are recorded in the audit log with before/after.
"""
from __future__ import annotations

from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.enums import AttributionStatus, EntryType, RealKind, Role
from app.models.ledger import LedgerEntry
from app.models.real import RealTransaction
from app.models.user import Member
from app.services import audit_service
from app.services.errors import (
    ConflictError,
    ForbiddenError,
    NotFoundError,
    ValidationError,
)
from app.util.time import utcnow


def _check_permission(actor: Member, group: list[LedgerEntry]) -> None:
    """Members may modify only their own records; admins may modify any."""
    if actor.role == Role.ADMIN.value:
        return
    owners = {e.member_id for e in group}
    if actor.id not in owners:
        raise ForbiddenError("只能修改自己的紀錄")


async def _group(session: AsyncSession, entry: LedgerEntry) -> list[LedgerEntry]:
    if entry.transfer_group_id:
        rows = (
            await session.execute(
                select(LedgerEntry).where(
                    LedgerEntry.transfer_group_id == entry.transfer_group_id
                )
            )
        ).scalars().all()
        return list(rows)
    return [entry]


async def _free_real_txn(session: AsyncSession, entry: LedgerEntry) -> None:
    if entry.source_real_txn_id is None:
        return
    rt = await session.get(RealTransaction, entry.source_real_txn_id)
    if rt is not None:
        rt.attribution_status = AttributionStatus.UNATTRIBUTED.value
        rt.attributed_member_id = None
        rt.attributed_by = None
        rt.attributed_at = None
        rt.ledger_entry_id = None


async def delete_entry(session: AsyncSession, *, actor: Member, entry_id: int) -> None:
    entry = await session.get(LedgerEntry, entry_id)
    if entry is None:
        raise NotFoundError("紀錄不存在")
    group = await _group(session, entry)
    _check_permission(actor, group)

    detail = {
        "ids": [e.id for e in group],
        "type": entry.entry_type,
        "points": [e.points_delta for e in group],
    }
    for e in group:
        await _free_real_txn(session, e)
        await session.delete(e)
    await audit_service.record(
        session, actor_id=actor.id, action="ledger.delete",
        target_type="ledger", target_id=entry_id, detail=detail,
    )
    await session.commit()


async def edit_entry(
    session: AsyncSession,
    *,
    actor: Member,
    entry_id: int,
    points: int | None = None,
    money_nt: Decimal | float | str | None = None,
    note: str | None = None,
) -> LedgerEntry:
    entry = await session.get(LedgerEntry, entry_id)
    if entry is None:
        raise NotFoundError("紀錄不存在")
    if entry.transfer_group_id:
        raise ValidationError("轉點紀錄不可編輯，請刪除後重新建立")
    _check_permission(actor, [entry])

    before = {
        "points": entry.points_delta,
        "money_nt": str(entry.money_nt) if entry.money_nt is not None else None,
        "note": entry.note,
    }

    if points is not None:
        if entry.source_real_txn_id is not None:
            raise ValidationError("已歸戶的紀錄金額不可編輯（可改備註或刪除後重歸戶）")
        if entry.entry_type == EntryType.TOPUP.value:
            if points <= 0:
                raise ValidationError("點數需為正數")
            entry.points_delta = points
        elif entry.entry_type == EntryType.PLAY.value:
            if points <= 0:
                raise ValidationError("點數需為正數")
            entry.points_delta = -points
        elif entry.entry_type == EntryType.ADJUSTMENT.value:
            if points == 0:
                raise ValidationError("調整不可為 0")
            entry.points_delta = points
        else:
            raise ValidationError("此類型不可編輯金額")

    if money_nt is not None:
        if entry.entry_type != EntryType.TOPUP.value:
            raise ValidationError("只有儲值有金額欄位")
        m = Decimal(str(money_nt))
        if m <= 0:
            raise ValidationError("金額需為正數")
        entry.money_nt = m

    if note is not None:
        entry.note = note

    await audit_service.record(
        session, actor_id=actor.id, action="ledger.edit",
        target_type="ledger", target_id=entry_id,
        detail={
            "before": before,
            "after": {
                "points": entry.points_delta,
                "money_nt": str(entry.money_nt) if entry.money_nt is not None else None,
                "note": entry.note,
            },
        },
    )
    await session.commit()
    await session.refresh(entry)
    return entry


async def attribute_existing(
    session: AsyncSession, *, actor: Member, entry_id: int, real_txn_id: int
) -> LedgerEntry:
    """補歸戶: link an existing manual top-up/play entry to a matching real txn.

    Marks the real txn attributed to the entry's owner and links it back, so a
    previously evidence-less manual entry becomes reconciled. The points/kind
    must match the real transaction.
    """
    entry = await session.get(LedgerEntry, entry_id)
    if entry is None:
        raise NotFoundError("紀錄不存在")
    _check_permission(actor, [entry])
    if entry.transfer_group_id is not None or entry.entry_type not in (
        EntryType.TOPUP.value, EntryType.PLAY.value
    ):
        raise ValidationError("此類型不可補歸戶")
    if entry.source_real_txn_id is not None:
        raise ConflictError("此筆已歸戶")

    rt = await session.get(RealTransaction, real_txn_id)
    if rt is None:
        raise NotFoundError("真實交易不存在")
    if rt.attribution_status != AttributionStatus.UNATTRIBUTED.value:
        raise ConflictError("該真實交易已被歸戶")
    expected_kind = (
        RealKind.TOPUP.value if entry.entry_type == EntryType.TOPUP.value
        else RealKind.PAY.value
    )
    if rt.kind != expected_kind:
        raise ValidationError("交易類型不符")
    if abs(rt.value) != abs(entry.points_delta):
        raise ValidationError("點數不符")

    entry.source_real_txn_id = rt.id
    rt.attribution_status = AttributionStatus.ATTRIBUTED.value
    rt.attributed_member_id = entry.member_id
    rt.attributed_by = actor.id
    rt.attributed_at = utcnow()
    rt.ledger_entry_id = entry.id
    try:
        await session.flush()  # partial unique index guards double-attribution
    except IntegrityError:
        await session.rollback()
        raise ConflictError("此筆已歸戶")
    await audit_service.record(
        session, actor_id=actor.id, action="ledger.attribute",
        target_type="ledger", target_id=entry_id, detail={"real_txn_id": rt.id},
    )
    await session.commit()
    await session.refresh(entry)
    return entry
