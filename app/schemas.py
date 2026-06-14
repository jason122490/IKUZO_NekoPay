"""Pydantic v2 request/response schemas."""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field


# ---------------- auth / members ----------------
class LoginIn(BaseModel):
    username: str
    password: str


class MemberCreateIn(BaseModel):
    username: str = Field(min_length=1, max_length=255)
    display_name: str = Field(min_length=1, max_length=100)
    password: str = Field(min_length=6)
    role: str = "member"


class MemberOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    username: str
    display_name: str
    role: str
    is_active: bool
    auto_attribute: bool = True


class MemberUpdateIn(BaseModel):
    display_name: str | None = Field(default=None, min_length=1, max_length=100)
    role: str | None = None


class StatusIn(BaseModel):
    is_active: bool


class ResetPasswordIn(BaseModel):
    new_password: str = Field(min_length=6)


class LoginOut(BaseModel):
    member: MemberOut
    csrf_token: str


class BalanceOut(BaseModel):
    member_id: int
    display_name: str
    points_balance: int
    money_contributed: Decimal


# ---------------- the four actions ----------------
class TopUpIn(BaseModel):
    member_id: int
    money_nt: Decimal = Field(gt=0)  # NT$ paid; points = floor(money/rate) + VIP bonus
    note: str | None = None
    idempotency_key: str | None = None


class RateIn(BaseModel):
    rate: Decimal = Field(gt=0)  # NT$ per point


class RateOut(BaseModel):
    rate: Decimal


class SyncSinceIn(BaseModel):
    since: str | None = None  # YYYY-MM-DD, or null/empty to clear the cutoff


class SyncSinceOut(BaseModel):
    since: str | None


class ResetIn(BaseModel):
    password: str  # re-entered to confirm the (irreversible) database reset


class PlayIn(BaseModel):
    member_id: int
    points: int = Field(gt=0)
    note: str | None = None
    idempotency_key: str | None = None
    allow_negative: bool = False


class TransferIn(BaseModel):
    from_member_id: int
    to_member_id: int
    points: int = Field(gt=0)
    note: str | None = None
    idempotency_key: str | None = None


class AdjustmentIn(BaseModel):
    member_id: int
    points_delta: int
    reason: str = Field(min_length=1)


class EditEntryIn(BaseModel):
    points: int | None = None       # magnitude (sign derived from entry type)
    money_nt: Decimal | None = None  # top-ups only
    note: str | None = None


class LinkRealIn(BaseModel):
    real_txn_id: int  # 歸戶: real transaction to link an existing entry to
    overwrite_note: bool = False  # overwrite the note with the real txn's name


class LedgerEntryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    member_id: int
    entry_type: str
    points_delta: int
    money_nt: Decimal | None
    note: str | None
    transfer_group_id: str | None
    source_real_txn_id: int | None
    created_at: datetime


class TransferOut(BaseModel):
    transfer_group_id: str
    out_entry: LedgerEntryOut
    in_entry: LedgerEntryOut


# ---------------- real txns / attribution ----------------
class RealTxnOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    kind: str
    shop: str
    machine: str | None
    raw_name: str
    value: int
    pay_type: str | None
    occurred_at: datetime
    attribution_status: str
    attributed_member_id: int | None


class AttributeIn(BaseModel):
    member_id: int


class MatchIn(BaseModel):
    kind: str = Field(pattern="^(topup|pay)$")
    points: int = Field(gt=0)


class CandidatesOut(BaseModel):
    candidates: list[RealTxnOut]
    synced: bool


class SelfAttributeIn(BaseModel):
    money_nt: Decimal | None = None  # for top-ups: the real NT$ the member paid


class AutoAttributeIn(BaseModel):
    enabled: bool


class IgnoreIn(BaseModel):
    reason: str = Field(min_length=1)


class ReconciliationOut(BaseModel):
    internal_total: int
    pooled_balance: int | None
    drift: int | None
    snapshot_age_sec: int | None
    unattributed_count: int
    unattributed_value: int
    manual_entry_count: int


# ---------------- analytics / settlement ----------------
class PositionOut(BaseModel):
    member_id: int
    display_name: str
    contributed_nt: Decimal
    consumed_points: int
    consumed_value_nt: Decimal
    balance_points: int
    balance_value_nt: Decimal
    fairness_net_nt: Decimal


class SettlementTxnOut(BaseModel):
    from_member_id: int
    from_name: str
    to_member_id: int
    to_name: str
    amount_nt: Decimal


class SettlementOut(BaseModel):
    rate_nt_per_point: Decimal
    positions: list[PositionOut]
    transactions: list[SettlementTxnOut]


class SyncRunOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    started_at: datetime
    finished_at: datetime | None
    status: str
    rows_seen: int
    rows_inserted: int
    error: str | None


class MessageOut(BaseModel):
    detail: str
