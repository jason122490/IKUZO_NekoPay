"""Synced-from-NekoPay models: real transactions, balance snapshots, sync runs.

`real_transactions` mirror getPayHistory rows for reconciliation + assisted
attribution. They are append-only and deduplicated by `dedup_key`.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models.enums import AttributionStatus
from app.util.time import utcnow


class RealTransaction(Base):
    __tablename__ = "real_transactions"

    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(8), nullable=False)  # topup | pay
    shop: Mapped[str] = mapped_column(String(120))
    machine: Mapped[str | None] = mapped_column(String(120), nullable=True)
    raw_name: Mapped[str] = mapped_column(String(255), nullable=False)
    value: Mapped[int] = mapped_column(Integer, nullable=False)  # signed: topup+, pay-
    pay_type: Mapped[str | None] = mapped_column(String(16), nullable=True)

    occurred_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    occurred_date_raw: Mapped[str] = mapped_column(String(8))  # "MM/DD"
    occurred_time_raw: Mapped[str] = mapped_column(String(8))  # "HH:MM"

    # Content identity of the record (excludes occurrence_index); used to count
    # how many records of the same content already exist when deduplicating.
    base_hash: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    dedup_key: Mapped[str] = mapped_column(
        String(64), unique=True, index=True, nullable=False
    )
    occurrence_index: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    first_seen_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    attribution_status: Mapped[str] = mapped_column(
        String(16), default=AttributionStatus.UNATTRIBUTED.value, index=True
    )
    attributed_member_id: Mapped[int | None] = mapped_column(
        ForeignKey("members.id"), nullable=True, index=True
    )
    attributed_by: Mapped[int | None] = mapped_column(
        ForeignKey("members.id"), nullable=True
    )
    attributed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # Plain int (no FK) to avoid a circular FK with ledger_entries; the
    # authoritative link is LedgerEntry.source_real_txn_id.
    ledger_entry_id: Mapped[int | None] = mapped_column(Integer, nullable=True)


class AccountSnapshot(Base):
    __tablename__ = "account_snapshots"

    id: Mapped[int] = mapped_column(primary_key=True)
    captured_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    balance: Mapped[int] = mapped_column(Integer)
    card_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    ticket_point: Mapped[int | None] = mapped_column(Integer, nullable=True)
    vip_name: Mapped[str | None] = mapped_column(String(32), nullable=True)
    vip_next_value: Mapped[int | None] = mapped_column(Integer, nullable=True)
    vip_cumulative: Mapped[int | None] = mapped_column(Integer, nullable=True)
    is_premium: Mapped[bool | None] = mapped_column(nullable=True)
    raw_json: Mapped[str | None] = mapped_column(Text, nullable=True)


class SyncRun(Base):
    __tablename__ = "sync_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    status: Mapped[str] = mapped_column(String(20))
    rows_seen: Mapped[int] = mapped_column(Integer, default=0)
    rows_inserted: Mapped[int] = mapped_column(Integer, default=0)
    window_gap_warning: Mapped[bool] = mapped_column(default=False)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)


class SyncState(Base):
    """Single-row warm-start cache for the encrypted NekoPay token."""

    __tablename__ = "sync_state"

    id: Mapped[int] = mapped_column(primary_key=True, default=1)
    enc_token: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
