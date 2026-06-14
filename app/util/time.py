"""Time helpers.

Internally we store *naive UTC* datetimes (no tzinfo) to avoid aware/naive
comparison surprises with SQLite round-trips. Display conversion to the app
timezone happens at the edge.
"""
from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo


def utcnow() -> datetime:
    """Current UTC time as a naive datetime."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def to_local(dt: datetime, tz_name: str) -> datetime:
    """Convert a naive-UTC datetime to a naive local datetime in tz_name."""
    aware = dt.replace(tzinfo=timezone.utc)
    return aware.astimezone(ZoneInfo(tz_name)).replace(tzinfo=None)


def local_now(tz_name: str) -> datetime:
    """Current local time (naive) in tz_name."""
    return datetime.now(ZoneInfo(tz_name)).replace(tzinfo=None)


def local_to_utc(naive_local: datetime, tz_name: str) -> datetime:
    """Convert a naive local datetime in tz_name to naive UTC."""
    aware = naive_local.replace(tzinfo=ZoneInfo(tz_name))
    return aware.astimezone(timezone.utc).replace(tzinfo=None)
