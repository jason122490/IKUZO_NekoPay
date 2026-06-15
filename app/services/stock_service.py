"""Fetch the 0050 (Yuanta Taiwan 50 ETF) closing price from TWSE, cached daily.

Powers a dashboard easter egg that expresses NT$ amounts as "shares of 0050".
The quote is refreshed at most once per Taipei calendar day (a settled closing
price is all the easter egg needs); on any error we serve the last known price
(or None, so the caller falls back to a hardcoded estimate) — the page never
breaks on a flaky/blocked upstream.
"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo

import httpx

_TWSE_URL = "https://mis.twse.com.tw/stock/api/getStockInfo.jsp"
_MARKET_TZ = ZoneInfo("Asia/Taipei")  # TWSE trades/closes on Taipei time
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
    ),
    "Referer": "https://mis.twse.com.tw/stock/index.jsp",
}

# module-level cache (single-worker app): {"price": Decimal|None, "day": date}
_cache: dict[str, object] = {"price": None, "day": None}


def _parse_price(data: dict) -> Decimal | None:
    arr = data.get("msgArray") or []
    if not arr:
        return None
    row = arr[0]
    # Prefer y (previous close = a settled closing price that changes once per
    # trading day); fall back to z (last trade) then o (open) if absent.
    for key in ("y", "z", "o"):
        raw = row.get(key)
        if raw and raw not in ("-", "0.0000"):
            try:
                return Decimal(raw).quantize(Decimal("0.01"))
            except InvalidOperation:
                continue
    return None


async def get_0050_price(*, proxy: str | None = None) -> Decimal | None:
    """0050 closing price in NT$, or None if unavailable. Refreshed once a day."""
    today: date = datetime.now(_MARKET_TZ).date()
    cached = _cache.get("price")
    if cached is not None and _cache.get("day") == today:
        return cached  # type: ignore[return-value]
    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(6.0, connect=4.0),
            headers=_HEADERS,
            proxy=proxy or None,
        ) as client:
            resp = await client.get(
                _TWSE_URL,
                params={"ex_ch": "tse_0050.tw", "json": "1", "delay": "0"},
            )
            resp.raise_for_status()
            price = _parse_price(resp.json())
    except (httpx.HTTPError, ValueError):
        price = None
    if price is not None:
        _cache["price"] = price
        _cache["day"] = today
        return price
    return _cache.get("price")  # type: ignore[return-value]  # stale-but-good, or None
