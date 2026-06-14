"""NekoPay VIP tier reference (from the official 2026 H1 Premium table).

The shared account has one tier (user_info.vipName). Thresholds are cumulative
top-up (NT$) per half-year cycle.
"""
from __future__ import annotations

from decimal import Decimal

VIP_TIERS = [
    {"name": "喵民", "card": "普卡", "premium": False, "bonus_pct": 10,
     "bonus": "+10%", "monthly_gift": 0, "vip_point": 0, "threshold": 0},
    {"name": "銀喵", "card": "Premium", "premium": True, "bonus_pct": 10,
     "bonus": "+10%", "monthly_gift": 15, "vip_point": 20, "threshold": 12000},
    {"name": "金喵", "card": "Premium", "premium": True, "bonus_pct": 15,
     "bonus": "+15%", "monthly_gift": 15, "vip_point": 40, "threshold": 27000},
    {"name": "喵皇", "card": "Premium", "premium": True, "bonus_pct": 15,
     "bonus": "+15%", "monthly_gift": 50, "vip_point": 70, "threshold": 45000},
]

# Single top-up must reach this NT$ amount to earn the tier's top-up bonus.
BONUS_MIN_TOPUP = 300

CYCLE = "2026/1/1 ~ 2026/6/30（每半年重新計算）"


def next_tier(current_name: str | None) -> dict | None:
    for i, t in enumerate(VIP_TIERS):
        if t["name"] == current_name:
            return VIP_TIERS[i + 1] if i + 1 < len(VIP_TIERS) else None
    return None


def vip_cumulative(event, preferred_key: str = "2009") -> int | None:
    """Current-cycle cumulative top-up (NT$) from user_info.event.

    event looks like {"2009": "6900"} where the key is the cycle's campaign id.
    Prefer that key; if it changed (new cycle), fall back to the largest numeric
    value present. Returns None if nothing usable.
    """
    if not isinstance(event, dict):
        return None
    v = event.get(preferred_key)
    if v is not None and str(v).strip().isdigit():
        return int(v)
    nums = [int(x) for x in event.values() if str(x).strip().isdigit()]
    return max(nums) if nums else None


def bonus_pct_for(vip_name: str | None) -> int:
    """Top-up bonus % for a tier name; 0 if the tier is unknown (not synced)."""
    for t in VIP_TIERS:
        if t["name"] == vip_name:
            return t["bonus_pct"]
    return 0


def topup_breakdown(
    money, rate, bonus_pct: int, min_topup: int = BONUS_MIN_TOPUP
) -> dict:
    """Points credited for a NT$ top-up.

    base  = floor(money / rate)
    bonus = floor(base * bonus_pct / 100)  -- only if money >= min_topup
    total = base + bonus
    """
    money = Decimal(str(money))
    rate = Decimal(str(rate))
    base = int(money // rate) if rate > 0 else 0
    bonus = (base * bonus_pct) // 100 if money >= min_topup else 0
    return {"base": base, "bonus": int(bonus), "total": base + int(bonus)}
