"""NekoPay VIP tier reference (from the official 2026 H1 Premium table).

The shared account has one tier (user_info.vipName). Thresholds are cumulative
top-up (NT$) per half-year cycle.
"""
from __future__ import annotations

VIP_TIERS = [
    {"name": "喵民", "card": "普卡", "premium": False,
     "bonus": "+10%", "monthly_gift": 0, "vip_point": 0, "threshold": 0},
    {"name": "銀喵", "card": "Premium", "premium": True,
     "bonus": "+10%", "monthly_gift": 15, "vip_point": 20, "threshold": 12000},
    {"name": "金喵", "card": "Premium", "premium": True,
     "bonus": "+15%", "monthly_gift": 15, "vip_point": 40, "threshold": 27000},
    {"name": "喵皇", "card": "Premium", "premium": True,
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
