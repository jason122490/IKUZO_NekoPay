"""Fetch the 0050 (Yuanta Taiwan 50 ETF) spot price from TWSE, cached in-process.

Powers a dashboard easter egg that expresses NT$ amounts as "shares of 0050".
TWSE's realtime quote endpoint is hit at most once per CACHE_TTL; on any error
we serve the last known price (or None, so the caller falls back to a hardcoded
estimate) — the page never breaks on a flaky/blocked upstream.
"""
from __future__ import annotations

import time
from decimal import Decimal, InvalidOperation

import httpx

_TWSE_URL = "https://mis.twse.com.tw/stock/api/getStockInfo.jsp"
_CACHE_TTL = 900.0  # seconds (15 min); an easter egg doesn't need finer
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
    ),
    "Referer": "https://mis.twse.com.tw/stock/index.jsp",
}

# module-level cache (single-worker app): {"price": Decimal|None, "at": monotonic}
_cache: dict[str, object] = {"price": None, "at": 0.0}


def _parse_price(data: dict) -> Decimal | None:
    arr = data.get("msgArray") or []
    if not arr:
        return None
    row = arr[0]
    # z = last trade; "-" when no trade yet / market closed -> fall back to
    # y (previous close), then o (open).
    for key in ("z", "y", "o"):
        raw = row.get(key)
        if raw and raw not in ("-", "0.0000"):
            try:
                return Decimal(raw).quantize(Decimal("0.01"))
            except InvalidOperation:
                continue
    return None


async def get_0050_price(*, proxy: str | None = None) -> Decimal | None:
    """Current 0050 price in NT$, or None if unavailable. Cached for CACHE_TTL."""
    now = time.monotonic()
    cached = _cache.get("price")
    if cached is not None and now - float(_cache["at"]) < _CACHE_TTL:
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
        _cache["at"] = now
        return price
    return _cache.get("price")  # type: ignore[return-value]  # stale-but-good, or None
