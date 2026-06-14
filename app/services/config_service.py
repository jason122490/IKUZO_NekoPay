"""App-level settings stored in the DB (NT$ <-> points rate)."""
from __future__ import annotations

from decimal import Decimal, InvalidOperation

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit import AppSetting

RATE_KEY = "rate_nt_per_point"
SYNC_SINCE_KEY = "sync_since_date"  # YYYY-MM-DD; ingest only real txns on/after


async def get_rate(session: AsyncSession, default: float | Decimal) -> Decimal:
    row = await session.get(AppSetting, RATE_KEY)
    if row is not None:
        try:
            return Decimal(row.value)
        except InvalidOperation:
            pass
    return Decimal(str(default))


async def set_rate(session: AsyncSession, value: Decimal | float | str) -> Decimal:
    rate = Decimal(str(value))
    row = await session.get(AppSetting, RATE_KEY)
    if row is None:
        session.add(AppSetting(key=RATE_KEY, value=str(rate)))
    else:
        row.value = str(rate)
    await session.commit()
    return rate


async def get_sync_since(session: AsyncSession) -> str | None:
    row = await session.get(AppSetting, SYNC_SINCE_KEY)
    return row.value if row and row.value else None


async def set_sync_since(session: AsyncSession, value: str | None) -> str | None:
    row = await session.get(AppSetting, SYNC_SINCE_KEY)
    if value:
        if row is None:
            session.add(AppSetting(key=SYNC_SINCE_KEY, value=value))
        else:
            row.value = value
    elif row is not None:
        await session.delete(row)
    await session.commit()
    return value
