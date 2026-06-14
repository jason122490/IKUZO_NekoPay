"""Reconciliation: compare the internal ledger against the real pooled balance."""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.enums import AttributionStatus, EntryType
from app.models.ledger import LedgerEntry
from app.models.real import AccountSnapshot, RealTransaction
from app.util.time import utcnow


@dataclass
class Reconciliation:
    internal_total: int
    pooled_balance: int | None
    drift: int | None
    snapshot_age_sec: int | None
    unattributed_count: int
    unattributed_value: int
    manual_entry_count: int  # internal topup/play with no real-txn evidence


async def reconcile_report(session: AsyncSession) -> Reconciliation:
    internal_total = int(
        (
            await session.execute(
                select(func.coalesce(func.sum(LedgerEntry.points_delta), 0))
            )
        ).scalar_one()
    )

    snap = (
        await session.execute(
            select(AccountSnapshot).order_by(AccountSnapshot.captured_at.desc()).limit(1)
        )
    ).scalar_one_or_none()

    pooled_balance = snap.balance if snap else None
    drift = (internal_total - pooled_balance) if pooled_balance is not None else None
    snapshot_age = (
        int((utcnow() - snap.captured_at).total_seconds()) if snap else None
    )

    unattributed_count, unattributed_value = (
        await session.execute(
            select(
                func.count(),
                func.coalesce(func.sum(RealTransaction.value), 0),
            ).where(
                RealTransaction.attribution_status
                == AttributionStatus.UNATTRIBUTED.value
            )
        )
    ).one()

    manual_entry_count = int(
        (
            await session.execute(
                select(func.count()).where(
                    LedgerEntry.source_real_txn_id.is_(None),
                    LedgerEntry.entry_type.in_(
                        [EntryType.TOPUP.value, EntryType.PLAY.value]
                    ),
                )
            )
        ).scalar_one()
    )

    return Reconciliation(
        internal_total=internal_total,
        pooled_balance=pooled_balance,
        drift=drift,
        snapshot_age_sec=snapshot_age,
        unattributed_count=int(unattributed_count),
        unattributed_value=int(unattributed_value),
        manual_entry_count=manual_entry_count,
    )
