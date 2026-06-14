"""Pure deduplication + parsing logic for getPayHistory rows.

This is the correctness-critical core of the sync layer. It must be:
  * idempotent  - re-seeing the same recent window inserts nothing new;
  * lossless    - rows that scroll out of the recent window are never deleted;
  * monotonic   - the count of identical-content records only ever grows,
                  so a genuinely new identical event (same shop/value/minute)
                  is detected and inserted rather than collapsed.

Identity model
--------------
``base_hash``  = sha256(kind | raw_name | signed_value | pay_type | date | time)
``dedup_key``  = sha256(base_hash : occurrence_index)

Because the API has no per-row id, no year, and only minute resolution, the
same (name, value, minute) can legitimately repeat. We disambiguate by an
``occurrence_index`` assigned as a contiguous continuation of however many
records with that ``base_hash`` already exist in the database. See ``reconcile``.
"""
from __future__ import annotations

import hashlib
from collections import defaultdict
from dataclasses import dataclass, replace
from datetime import datetime

from app.models.enums import RealKind
from app.util.time import local_to_utc

# A reconstructed datetime more than this many days in the "future" relative to
# now is interpreted as belonging to the previous year (no-year rollover).
FUTURE_SLACK_DAYS = 2


@dataclass
class ParsedRecord:
    kind: str
    shop: str
    machine: str | None
    raw_name: str
    value: int  # signed: topup +, pay -
    pay_type: str | None
    occurred_date_raw: str
    occurred_time_raw: str
    occurred_at: datetime  # naive UTC
    base_hash: str
    occurrence_index: int = 0
    dedup_key: str = ""


@dataclass
class ReconcileResult:
    to_insert: list[ParsedRecord]
    seen_keys: list[str]  # dedup_keys of already-known rows (bump last_seen)


def parse_name(kind: str, name: str) -> tuple[str, str | None]:
    """Split a record name into (shop, machine).

    pay names look like ``"竹喵店 - Chunithm"``; topup names are just the shop.
    """
    name = (name or "").strip()
    if kind == RealKind.PAY.value and " - " in name:
        shop, machine = name.split(" - ", 1)
        return shop.strip(), machine.strip()
    return name, None


def signed_value(kind: str, value) -> int:
    v = abs(int(value))
    return v if kind == RealKind.TOPUP.value else -v


def compute_base_hash(
    kind: str,
    raw_name: str,
    value_signed: int,
    pay_type: str | None,
    date_raw: str,
    time_raw: str,
) -> str:
    parts = [kind, raw_name, str(value_signed), pay_type or "", date_raw, time_raw]
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()


def compute_dedup_key(base_hash: str, occurrence_index: int) -> str:
    return hashlib.sha256(f"{base_hash}:{occurrence_index}".encode()).hexdigest()


def reconstruct_occurred_at(
    date_raw: str, time_raw: str, now_local: datetime, tz_name: str
) -> datetime:
    """Reconstruct a full naive-UTC datetime from ``MM/DD`` + ``HH:MM``.

    Assumes the current local year; if that lands more than FUTURE_SLACK_DAYS in
    the future (a late-December record seen in early January), roll back a year.
    """
    mm, dd = (int(x) for x in date_raw.split("/"))
    hh, minute = (int(x) for x in time_raw.split(":"))
    year = now_local.year
    try:
        candidate = datetime(year, mm, dd, hh, minute)
    except ValueError:
        # e.g. malformed; fall back to "now" so the row is still ingestible.
        return local_to_utc(now_local, tz_name)
    if (candidate - now_local).days > FUTURE_SLACK_DAYS:
        try:
            candidate = candidate.replace(year=year - 1)
        except ValueError:  # Feb 29 -> non-leap year
            candidate = candidate.replace(year=year - 1, day=28)
    return local_to_utc(candidate, tz_name)


def parse_history(
    data: dict, now_local: datetime, tz_name: str
) -> list[ParsedRecord]:
    """Normalize a getPayHistory ``data`` payload into ParsedRecords (in order)."""
    records: list[ParsedRecord] = []
    for key, kind in (("topup", RealKind.TOPUP.value), ("pay", RealKind.PAY.value)):
        for row in data.get(key) or []:
            t = row.get("time") or {}
            date_raw = str(t.get("date", ""))
            time_raw = str(t.get("time", ""))
            raw_name = str(row.get("name", ""))
            sval = signed_value(kind, row.get("value", 0))
            pay_type = row.get("type") if kind == RealKind.PAY.value else None
            shop, machine = parse_name(kind, raw_name)
            occurred_at = reconstruct_occurred_at(
                date_raw, time_raw, now_local, tz_name
            )
            records.append(
                ParsedRecord(
                    kind=kind,
                    shop=shop,
                    machine=machine,
                    raw_name=raw_name,
                    value=sval,
                    pay_type=pay_type,
                    occurred_date_raw=date_raw,
                    occurred_time_raw=time_raw,
                    occurred_at=occurred_at,
                    base_hash=compute_base_hash(
                        kind, raw_name, sval, pay_type, date_raw, time_raw
                    ),
                )
            )
    return records


def reconcile(
    records: list[ParsedRecord], existing_counts: dict[str, int]
) -> ReconcileResult:
    """Decide which parsed records are new, given DB counts per base_hash.

    For each base_hash with N existing rows and M in this response:
      * indices 0..min(N,M)-1 are re-sightings -> bump last_seen;
      * if M > N, indices N..M-1 are genuinely new -> insert.
    This keeps the per-content count monotonic non-decreasing.
    """
    groups: dict[str, list[ParsedRecord]] = defaultdict(list)
    for r in records:
        groups[r.base_hash].append(r)

    to_insert: list[ParsedRecord] = []
    seen_keys: list[str] = []
    for base_hash, recs in groups.items():
        n = existing_counts.get(base_hash, 0)
        m = len(recs)
        for idx in range(min(n, m)):
            seen_keys.append(compute_dedup_key(base_hash, idx))
        for idx in range(n, m):
            new_rec = replace(
                recs[idx],
                occurrence_index=idx,
                dedup_key=compute_dedup_key(base_hash, idx),
            )
            to_insert.append(new_rec)
    return ReconcileResult(to_insert=to_insert, seen_keys=seen_keys)
