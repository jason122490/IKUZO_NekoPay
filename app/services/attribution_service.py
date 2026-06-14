"""Assisted attribution: turn a synced real transaction into a ledger entry.

Race-safety: the authoritative guard is the partial unique index on
ledger_entries(source_real_txn_id) WHERE reversal_of_id IS NULL. Two concurrent
attributions of the same real txn -> the second insert raises IntegrityError ->
ConflictError. The status pre-check is just a friendly fast path.
"""
from __future__ import annotations

from decimal import Decimal

from sqlalchemy import update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.enums import AttributionStatus, ClaimStatus, EntryType, RealKind
from app.models.ledger import AttributionClaim, LedgerEntry
from app.models.real import RealTransaction
from app.services import audit_service
from app.services.errors import ConflictError, NotFoundError, ValidationError
from app.services.ledger_service import _require_active_member, reverse_entry
from app.util.time import utcnow


async def attribute(
    session: AsyncSession,
    *,
    real_txn_id: int,
    member_id: int,
    actor_id: int,
    rate: Decimal,
    money_nt: Decimal | None = None,
) -> LedgerEntry:
    rt = await session.get(RealTransaction, real_txn_id)
    if rt is None:
        raise NotFoundError(f"real transaction {real_txn_id} not found")
    if rt.attribution_status != AttributionStatus.UNATTRIBUTED.value:
        raise ConflictError("transaction already attributed or ignored")
    await _require_active_member(session, member_id)

    points = abs(rt.value)
    if rt.kind == RealKind.TOPUP.value:
        # use the real NT$ the member paid if provided, else points * rate
        money = money_nt if money_nt is not None else (Decimal(points) * rate)
        entry = LedgerEntry(
            member_id=member_id,
            entry_type=EntryType.TOPUP.value,
            points_delta=points,
            money_nt=money,
            note=f"attributed top-up: {rt.raw_name}",
            created_by=actor_id,
            source_real_txn_id=rt.id,
        )
    else:
        entry = LedgerEntry(
            member_id=member_id,
            entry_type=EntryType.PLAY.value,
            points_delta=-points,
            note=f"attributed play: {rt.raw_name}",
            created_by=actor_id,
            source_real_txn_id=rt.id,
        )

    rt.attribution_status = AttributionStatus.ATTRIBUTED.value
    rt.attributed_member_id = member_id
    rt.attributed_by = actor_id
    rt.attributed_at = utcnow()
    session.add(entry)
    try:
        await session.flush()  # partial unique index enforces one entry per txn
    except IntegrityError:
        await session.rollback()
        raise ConflictError("transaction already has a ledger entry")
    rt.ledger_entry_id = entry.id
    await audit_service.record(
        session, actor_id=actor_id, action="attribution.attribute",
        target_type="real_txn", target_id=rt.id,
        detail={"member_id": member_id, "kind": rt.kind, "value": rt.value},
    )
    await session.commit()
    await session.refresh(entry)
    return entry


async def ignore_real_txn(
    session: AsyncSession, *, real_txn_id: int, actor_id: int, reason: str
) -> RealTransaction:
    if not reason:
        raise ValidationError("a reason is required to ignore a transaction")
    res = await session.execute(
        update(RealTransaction)
        .where(
            RealTransaction.id == real_txn_id,
            RealTransaction.attribution_status == AttributionStatus.UNATTRIBUTED.value,
        )
        .values(attribution_status=AttributionStatus.IGNORED.value)
    )
    if res.rowcount == 0:
        raise ConflictError("transaction not found or not unattributed")
    await session.commit()
    return await session.get(RealTransaction, real_txn_id)


async def reverse_attribution(
    session: AsyncSession, *, real_txn_id: int, actor_id: int, reason: str
) -> RealTransaction:
    rt = await session.get(RealTransaction, real_txn_id)
    if rt is None or rt.ledger_entry_id is None:
        raise NotFoundError("attributed transaction not found")
    await reverse_entry(
        session, entry_id=rt.ledger_entry_id, created_by=actor_id, reason=reason
    )
    rt.attribution_status = AttributionStatus.UNATTRIBUTED.value
    rt.attributed_member_id = None
    rt.attributed_by = None
    rt.attributed_at = None
    rt.ledger_entry_id = None
    await session.commit()
    return rt


# ----------------------------------------------------------- member self-claim


async def create_claim(
    session: AsyncSession, *, real_txn_id: int, member_id: int
) -> AttributionClaim:
    rt = await session.get(RealTransaction, real_txn_id)
    if rt is None:
        raise NotFoundError(f"real transaction {real_txn_id} not found")
    if rt.attribution_status != AttributionStatus.UNATTRIBUTED.value:
        raise ConflictError("transaction already attributed or ignored")
    claim = AttributionClaim(real_txn_id=real_txn_id, member_id=member_id)
    session.add(claim)
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        raise ConflictError("you already have a pending claim for this transaction")
    await session.refresh(claim)
    return claim


async def approve_claim(
    session: AsyncSession, *, claim_id: int, actor_id: int, rate: Decimal
) -> LedgerEntry:
    claim = await session.get(AttributionClaim, claim_id)
    if claim is None or claim.status != ClaimStatus.PENDING.value:
        raise NotFoundError("pending claim not found")
    entry = await attribute(
        session,
        real_txn_id=claim.real_txn_id,
        member_id=claim.member_id,
        actor_id=actor_id,
        rate=rate,
    )
    claim.status = ClaimStatus.APPROVED.value
    claim.resolved_at = utcnow()
    claim.resolved_by = actor_id
    # auto-reject other pending claims for the same transaction
    await session.execute(
        update(AttributionClaim)
        .where(
            AttributionClaim.real_txn_id == claim.real_txn_id,
            AttributionClaim.status == ClaimStatus.PENDING.value,
            AttributionClaim.id != claim.id,
        )
        .values(
            status=ClaimStatus.REJECTED.value,
            resolved_at=utcnow(),
            resolved_by=actor_id,
        )
    )
    await session.commit()
    return entry


async def reject_claim(
    session: AsyncSession, *, claim_id: int, actor_id: int
) -> AttributionClaim:
    claim = await session.get(AttributionClaim, claim_id)
    if claim is None or claim.status != ClaimStatus.PENDING.value:
        raise NotFoundError("pending claim not found")
    claim.status = ClaimStatus.REJECTED.value
    claim.resolved_at = utcnow()
    claim.resolved_by = actor_id
    await session.commit()
    return claim
