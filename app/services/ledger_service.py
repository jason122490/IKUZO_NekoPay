"""Append-only ledger operations: top-up, play, transfer, adjustment, reversal.

Invariants enforced here (not just in the DB):
  * balances are derived (SUM of points_delta), never stored;
  * a transfer writes exactly two rows summing to zero, in one transaction,
    so it never changes the reconciliation total;
  * idempotency_key makes a retried submit return the original entry;
  * by default a member cannot go negative (admin may override -> debt).
"""
from __future__ import annotations

import uuid
from decimal import Decimal, InvalidOperation

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.enums import EntryType
from app.models.ledger import LedgerEntry
from app.models.user import Member
from app.services.errors import (
    InsufficientBalanceError,
    NotFoundError,
    ValidationError,
)


# ---------------------------------------------------------------- balances


async def get_balance(session: AsyncSession, member_id: int) -> int:
    res = await session.execute(
        select(func.coalesce(func.sum(LedgerEntry.points_delta), 0)).where(
            LedgerEntry.member_id == member_id
        )
    )
    return int(res.scalar_one())


async def get_money_contributed(session: AsyncSession, member_id: int) -> Decimal:
    res = await session.execute(
        select(func.coalesce(func.sum(LedgerEntry.money_nt), 0)).where(
            LedgerEntry.member_id == member_id,
            LedgerEntry.money_nt.is_not(None),
        )
    )
    return Decimal(str(res.scalar_one()))


async def get_internal_total(session: AsyncSession) -> int:
    res = await session.execute(
        select(func.coalesce(func.sum(LedgerEntry.points_delta), 0))
    )
    return int(res.scalar_one())


# ---------------------------------------------------------------- helpers


async def _require_active_member(session: AsyncSession, member_id: int) -> Member:
    member = await session.get(Member, member_id)
    if member is None or not member.is_active:
        raise NotFoundError(f"member {member_id} not found or inactive")
    return member


async def _existing_by_key(
    session: AsyncSession, key: str | None
) -> LedgerEntry | None:
    if not key:
        return None
    res = await session.execute(
        select(LedgerEntry).where(LedgerEntry.idempotency_key == key)
    )
    return res.scalar_one_or_none()


def _to_decimal(value) -> Decimal:
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError):
        raise ValidationError("invalid money amount")


# ---------------------------------------------------------------- operations


async def record_topup(
    session: AsyncSession,
    *,
    member_id: int,
    points: int,
    money_nt,
    created_by: int,
    note: str | None = None,
    idempotency_key: str | None = None,
    source_real_txn_id: int | None = None,
) -> LedgerEntry:
    if points <= 0:
        raise ValidationError("points must be positive")
    money = _to_decimal(money_nt)
    if money <= 0:
        raise ValidationError("money_nt must be positive")
    await _require_active_member(session, member_id)

    existing = await _existing_by_key(session, idempotency_key)
    if existing is not None:
        return existing

    entry = LedgerEntry(
        member_id=member_id,
        entry_type=EntryType.TOPUP.value,
        points_delta=points,
        money_nt=money,
        note=note,
        created_by=created_by,
        source_real_txn_id=source_real_txn_id,
        idempotency_key=idempotency_key,
    )
    return await _commit_entry(session, entry, idempotency_key)


async def record_play(
    session: AsyncSession,
    *,
    member_id: int,
    points: int,
    created_by: int,
    note: str | None = None,
    idempotency_key: str | None = None,
    allow_negative: bool = False,
    source_real_txn_id: int | None = None,
) -> LedgerEntry:
    if points <= 0:
        raise ValidationError("points must be positive")
    await _require_active_member(session, member_id)

    existing = await _existing_by_key(session, idempotency_key)
    if existing is not None:
        return existing

    if not allow_negative:
        balance = await get_balance(session, member_id)
        if balance < points:
            raise InsufficientBalanceError(
                f"餘額不足：目前 {balance} 點，需要 {points} 點"
            )

    entry = LedgerEntry(
        member_id=member_id,
        entry_type=EntryType.PLAY.value,
        points_delta=-points,
        note=note,
        created_by=created_by,
        source_real_txn_id=source_real_txn_id,
        idempotency_key=idempotency_key,
    )
    return await _commit_entry(session, entry, idempotency_key)


async def transfer(
    session: AsyncSession,
    *,
    from_member_id: int,
    to_member_id: int,
    points: int,
    created_by: int,
    note: str | None = None,
    idempotency_key: str | None = None,
    allow_negative: bool = False,
) -> tuple[LedgerEntry, LedgerEntry]:
    if points <= 0:
        raise ValidationError("points must be positive")
    if from_member_id == to_member_id:
        raise ValidationError("cannot transfer to self")
    await _require_active_member(session, from_member_id)
    await _require_active_member(session, to_member_id)

    # Idempotency: keyed on the OUT row; return both sides if already done.
    if idempotency_key:
        existing = await _existing_by_key(session, idempotency_key)
        if existing is not None:
            res = await session.execute(
                select(LedgerEntry)
                .where(LedgerEntry.transfer_group_id == existing.transfer_group_id)
                .order_by(LedgerEntry.points_delta)  # OUT (neg) first
            )
            rows = list(res.scalars())
            out_row = next(r for r in rows if r.points_delta < 0)
            in_row = next(r for r in rows if r.points_delta > 0)
            return out_row, in_row

    if not allow_negative:
        balance = await get_balance(session, from_member_id)
        if balance < points:
            raise InsufficientBalanceError(
                f"餘額不足：目前 {balance} 點，需要 {points} 點"
            )

    group_id = str(uuid.uuid4())
    out_row = LedgerEntry(
        member_id=from_member_id,
        entry_type=EntryType.TRANSFER_OUT.value,
        points_delta=-points,
        note=note,
        created_by=created_by,
        transfer_group_id=group_id,
        idempotency_key=idempotency_key,
    )
    in_row = LedgerEntry(
        member_id=to_member_id,
        entry_type=EntryType.TRANSFER_IN.value,
        points_delta=points,
        note=note,
        created_by=created_by,
        transfer_group_id=group_id,
        idempotency_key=(f"{idempotency_key}:in" if idempotency_key else None),
    )
    session.add_all([out_row, in_row])
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        # Likely a concurrent identical transfer; return the persisted pair.
        if idempotency_key:
            return await transfer(
                session,
                from_member_id=from_member_id,
                to_member_id=to_member_id,
                points=points,
                created_by=created_by,
                note=note,
                idempotency_key=idempotency_key,
                allow_negative=allow_negative,
            )
        raise
    await session.refresh(out_row)
    await session.refresh(in_row)
    return out_row, in_row


async def record_adjustment(
    session: AsyncSession,
    *,
    member_id: int,
    points_delta: int,
    created_by: int,
    reason: str,
) -> LedgerEntry:
    if points_delta == 0:
        raise ValidationError("adjustment delta cannot be zero")
    if not reason:
        raise ValidationError("adjustment requires a reason")
    await _require_active_member(session, member_id)
    entry = LedgerEntry(
        member_id=member_id,
        entry_type=EntryType.ADJUSTMENT.value,
        points_delta=points_delta,
        note=reason,
        created_by=created_by,
    )
    return await _commit_entry(session, entry, None)


async def reverse_entry(
    session: AsyncSession,
    *,
    entry_id: int,
    created_by: int,
    reason: str,
) -> LedgerEntry:
    """Append a compensating ADJUSTMENT that negates an entry's points/money."""
    original = await session.get(LedgerEntry, entry_id)
    if original is None:
        raise NotFoundError(f"ledger entry {entry_id} not found")
    rev = LedgerEntry(
        member_id=original.member_id,
        entry_type=EntryType.ADJUSTMENT.value,
        points_delta=-original.points_delta,
        money_nt=(-original.money_nt if original.money_nt is not None else None),
        note=f"reversal of #{entry_id}: {reason}",
        created_by=created_by,
        reversal_of_id=entry_id,
    )
    return await _commit_entry(session, rev, None)


async def _commit_entry(
    session: AsyncSession, entry: LedgerEntry, idempotency_key: str | None
) -> LedgerEntry:
    session.add(entry)
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        existing = await _existing_by_key(session, idempotency_key)
        if existing is not None:
            return existing
        raise
    await session.refresh(entry)
    return entry
