"""VIP economics helpers (pure functions)."""
from __future__ import annotations

from app.vip import bonus_pct_for, next_tier, topup_breakdown, vip_cumulative


def test_vip_cumulative_prefers_cycle_key():
    assert vip_cumulative({"2009": "6900"}) == 6900
    assert vip_cumulative({"2010": "5000"}, preferred_key="2010") == 5000


def test_vip_cumulative_fallback_and_empty():
    # preferred key absent -> largest numeric value
    assert vip_cumulative({"2010": "5000", "x": "3000"}) == 5000
    assert vip_cumulative({}) is None
    assert vip_cumulative(None) is None
    assert vip_cumulative({"a": "abc"}) is None


def test_bonus_pct_for_tier():
    assert bonus_pct_for("喵民") == 10
    assert bonus_pct_for("銀喵") == 10
    assert bonus_pct_for("金喵") == 15
    assert bonus_pct_for("喵皇") == 15
    assert bonus_pct_for(None) == 0  # unknown / not synced


def test_topup_breakdown():
    # NT$300 at rate 10, 10% tier -> 30 + 3 = 33 (matches real getPayHistory data)
    assert topup_breakdown(300, 10, 10) == {"base": 30, "bonus": 3, "total": 33}
    # NT$3000, 15% -> 300 + 45 = 345
    assert topup_breakdown(3000, 10, 15) == {"base": 300, "bonus": 45, "total": 345}
    # below NT$300 threshold -> no bonus
    assert topup_breakdown(100, 10, 15) == {"base": 10, "bonus": 0, "total": 10}


def test_next_tier_chain():
    assert next_tier("喵民")["name"] == "銀喵"
    assert next_tier("金喵")["name"] == "喵皇"
    assert next_tier("喵皇") is None
