"""Append-only internal ledger + member self-claim models.

The ledger is event-sourced: a member's points balance is always
SUM(points_delta). Balance-bearing rows are never updated or deleted;
corrections are new ADJUSTMENT rows or a reversal (reversal_of_id).
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models.enums import ClaimStatus
from app.util.time import utcnow


class LedgerEntry(Base):
    __tablename__ = "ledger_entries"
    __table_args__ = (
        CheckConstraint(
            "(entry_type='TOPUP' AND points_delta>0 AND money_nt IS NOT NULL) OR "
            "(entry_type='PLAY' AND points_delta<0) OR "
            "(entry_type='TRANSFER_IN' AND points_delta>0) OR "
            "(entry_type='TRANSFER_OUT' AND points_delta<0) OR "
            "(entry_type='ADJUSTMENT' AND points_delta<>0)",
            name="ck_ledger_entry_sign",
        ),
        # At most one *active* (non-reversal) ledger entry per real transaction.
        Index(
            "uq_ledger_source_real_active",
            "source_real_txn_id",
            unique=True,
            sqlite_where=text(
                "source_real_txn_id IS NOT NULL AND reversal_of_id IS NULL"
            ),
        ),
        Index("ix_ledger_member_created", "member_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    member_id: Mapped[int] = mapped_column(
        ForeignKey("members.id"), index=True, nullable=False
    )
    entry_type: Mapped[str] = mapped_column(String(16), nullable=False)
    points_delta: Mapped[int] = mapped_column(Integer, nullable=False)
    money_nt: Mapped[Decimal | None] = mapped_column(Numeric(10, 2), nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[int] = mapped_column(ForeignKey("members.id"), nullable=False)
    transfer_group_id: Mapped[str | None] = mapped_column(
        String(36), nullable=True, index=True
    )
    source_real_txn_id: Mapped[int | None] = mapped_column(
        ForeignKey("real_transactions.id"), nullable=True, index=True
    )
    reversal_of_id: Mapped[int | None] = mapped_column(
        ForeignKey("ledger_entries.id"), nullable=True
    )
    idempotency_key: Mapped[str | None] = mapped_column(
        String(80), nullable=True, unique=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class AttributionClaim(Base):
    """A member's pending claim that a real transaction was theirs."""

    __tablename__ = "attribution_claims"
    __table_args__ = (
        Index(
            "uq_claim_pending_per_txn",
            "real_txn_id",
            "member_id",
            unique=True,
            sqlite_where=text("status='pending'"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    real_txn_id: Mapped[int] = mapped_column(
        ForeignKey("real_transactions.id"), index=True, nullable=False
    )
    member_id: Mapped[int] = mapped_column(ForeignKey("members.id"), nullable=False)
    status: Mapped[str] = mapped_column(String(16), default=ClaimStatus.PENDING.value)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    resolved_by: Mapped[int | None] = mapped_column(
        ForeignKey("members.id"), nullable=True
    )
