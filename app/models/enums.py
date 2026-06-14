"""Enumerations used across the data model (stored as VARCHAR).

Uses ``(str, Enum)`` for Python 3.10 compatibility (StrEnum is 3.11+).
"""
from __future__ import annotations

from enum import Enum


class Role(str, Enum):
    MEMBER = "member"
    ADMIN = "admin"


class EntryType(str, Enum):
    TOPUP = "TOPUP"
    PLAY = "PLAY"
    TRANSFER_IN = "TRANSFER_IN"
    TRANSFER_OUT = "TRANSFER_OUT"
    ADJUSTMENT = "ADJUSTMENT"


class RealKind(str, Enum):
    TOPUP = "topup"
    PAY = "pay"


class PayType(str, Enum):
    POINT = "point"
    TICKET = "ticket"


class AttributionStatus(str, Enum):
    UNATTRIBUTED = "unattributed"
    ATTRIBUTED = "attributed"
    IGNORED = "ignored"


class ClaimStatus(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class SyncStatus(str, Enum):
    OK = "ok"
    AUTH_FAILED = "auth_failed"
    TRANSPORT_FAILED = "transport_failed"
    PARTIAL = "partial"
