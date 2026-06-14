"""Tests for the correctness-critical dedup/parse logic (pure functions)."""
from __future__ import annotations

from datetime import datetime

from app.sync.dedup import (
    compute_dedup_key,
    parse_history,
    parse_name,
    reconcile,
    reconstruct_occurred_at,
    signed_value,
)

TZ = "Asia/Taipei"


def test_parse_name_pay_and_topup():
    assert parse_name("pay", "竹喵店 - Chunithm") == ("竹喵店", "Chunithm")
    assert parse_name("topup", "竹喵店") == ("竹喵店", None)
    assert parse_name("pay", "竹喵店") == ("竹喵店", None)


def test_signed_value():
    assert signed_value("topup", 33) == 33
    assert signed_value("pay", 3) == -3
    assert signed_value("pay", -3) == -3  # normalizes magnitude


def test_year_reconstruction_current_year():
    now_local = datetime(2026, 6, 14, 12, 0)
    dt = reconstruct_occurred_at("06/10", "18:07", now_local, TZ)
    # 2026-06-10 18:07 Asia/Taipei -> 10:07 UTC, same date
    assert dt.year == 2026 and dt.month == 6 and dt.day == 10


def test_year_reconstruction_rollover_to_previous_year():
    now_local = datetime(2026, 1, 2, 9, 0)
    dt = reconstruct_occurred_at("12/30", "23:00", now_local, TZ)
    assert dt.year == 2025 and dt.month == 12


def _payload(pay_rows=(), topup_rows=()):
    return {
        "topup": [
            {"time": {"date": d, "time": t}, "name": n, "value": v}
            for (d, t, n, v) in topup_rows
        ],
        "pay": [
            {"time": {"date": d, "time": t}, "name": n, "value": v, "type": "point"}
            for (d, t, n, v) in pay_rows
        ],
    }


NOW = datetime(2026, 6, 14, 12, 0)


def test_reconcile_first_sync_inserts_all():
    data = _payload(
        pay_rows=[("06/10", "20:47", "竹喵店 - Chunithm", 3)],
        topup_rows=[("06/10", "18:07", "竹喵店", 33)],
    )
    records = parse_history(data, NOW, TZ)
    result = reconcile(records, existing_counts={})
    assert len(result.to_insert) == 2
    assert result.seen_keys == []
    assert all(r.occurrence_index == 0 for r in result.to_insert)


def test_reconcile_replay_is_idempotent():
    data = _payload(pay_rows=[("06/10", "20:47", "竹喵店 - Chunithm", 3)])
    records = parse_history(data, NOW, TZ)
    bh = records[0].base_hash
    # DB already has 1 row for this content
    result = reconcile(records, existing_counts={bh: 1})
    assert result.to_insert == []
    assert result.seen_keys == [compute_dedup_key(bh, 0)]


def test_reconcile_legitimate_same_minute_duplicates():
    data = _payload(
        pay_rows=[
            ("06/10", "20:47", "竹喵店 - Chunithm", 3),
            ("06/10", "20:47", "竹喵店 - Chunithm", 3),
        ]
    )
    records = parse_history(data, NOW, TZ)
    result = reconcile(records, existing_counts={})
    assert len(result.to_insert) == 2
    assert sorted(r.occurrence_index for r in result.to_insert) == [0, 1]
    # distinct dedup keys
    assert len({r.dedup_key for r in result.to_insert}) == 2


def test_reconcile_new_identical_event_appends_one():
    data = _payload(
        pay_rows=[
            ("06/10", "20:47", "竹喵店 - Chunithm", 3),
            ("06/10", "20:47", "竹喵店 - Chunithm", 3),
            ("06/10", "20:47", "竹喵店 - Chunithm", 3),
        ]
    )
    records = parse_history(data, NOW, TZ)
    bh = records[0].base_hash
    # DB already has 2 of these; a third genuinely-new identical event appears
    result = reconcile(records, existing_counts={bh: 2})
    assert len(result.to_insert) == 1
    assert result.to_insert[0].occurrence_index == 2
    assert len(result.seen_keys) == 2


def test_reconcile_fewer_than_existing_inserts_nothing():
    # rows aged out: response shows fewer than DB has -> never delete, never insert
    data = _payload(pay_rows=[("06/10", "20:47", "竹喵店 - Chunithm", 3)])
    records = parse_history(data, NOW, TZ)
    bh = records[0].base_hash
    result = reconcile(records, existing_counts={bh: 3})
    assert result.to_insert == []
    assert result.seen_keys == [compute_dedup_key(bh, 0)]
