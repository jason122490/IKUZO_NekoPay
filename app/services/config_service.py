"""App-level settings stored in the DB (NT$ <-> points rate)."""
from __future__ import annotations

from decimal import Decimal, InvalidOperation

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit import AppSetting

RATE_KEY = "rate_nt_per_point"


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
