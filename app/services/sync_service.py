"""One NekoPay sync cycle: snapshot balance + upsert pay history (deduped).

Network fetches happen first (no DB writes), so a transport/auth failure never
leaves partial rows; all DB writes commit together. Every cycle records a
SyncRun row and never lets an exception escape to kill the scheduler.
"""
from __future__ import annotations

import asyncio
import json

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.enums import SyncStatus
from app.models.real import AccountSnapshot, RealTransaction, SyncRun
from app.services.nekopay_client import (
    NekoPayAuthError,
    NekoPayClient,
    NekoPayTransportError,
)
from app.services.token_manager import TokenManager
from app.sync.dedup import parse_history, reconcile
from app.util.time import local_now, utcnow


async def _fetch_with_retry(factory, tm: TokenManager, retries: int, backoff: float):
    last: Exception | None = None
    for attempt in range(retries):
        try:
            return await tm.call_with_retry(factory)
        except NekoPayTransportError as exc:
            last = exc
            if attempt < retries - 1 and backoff > 0:
                await asyncio.sleep(backoff * (2**attempt))
    raise last  # type: ignore[misc]


async def run_sync_cycle(
    session: AsyncSession,
    client: NekoPayClient,
    token_manager: TokenManager,
    tz_name: str,
    *,
    transport_retries: int = 3,
    backoff_base: float = 2.0,
) -> SyncRun:
    run = SyncRun(started_at=utcnow(), status=SyncStatus.OK.value)

    # --- network first (no DB writes yet) ---
    try:
        info = await _fetch_with_retry(
            lambda t: client.get_user_info(t), token_manager,
            transport_retries, backoff_base,
        )
        data = await _fetch_with_retry(
            lambda t: client.get_pay_history(t), token_manager,
            transport_retries, backoff_base,
        )
    except NekoPayAuthError as exc:
        run.status = SyncStatus.AUTH_FAILED.value
        run.error = str(exc)[:500]
        run.finished_at = utcnow()
        session.add(run)
        await session.commit()
        return run
    except NekoPayTransportError as exc:
        run.status = SyncStatus.TRANSPORT_FAILED.value
        run.error = str(exc)[:500]
        run.finished_at = utcnow()
        session.add(run)
        await session.commit()
        return run

    # --- DB writes (all together) ---
    try:
        session.add(
            AccountSnapshot(
                balance=int(info.get("balance", 0) or 0),
                card_id=info.get("cardId"),
                status=str(info.get("status")) if info.get("status") is not None else None,
                ticket_point=info.get("ticketPoint"),
                vip_name=info.get("vipName"),
                vip_next_value=info.get("vipNextValue"),
                is_premium=info.get("isPremium"),
                raw_json=json.dumps(info, ensure_ascii=False),
            )
        )

        records = parse_history(data, local_now(tz_name), tz_name)
        base_hashes = {r.base_hash for r in records}
        existing_counts: dict[str, int] = {}
        if base_hashes:
            res = await session.execute(
                select(RealTransaction.base_hash, func.count())
                .where(RealTransaction.base_hash.in_(base_hashes))
                .group_by(RealTransaction.base_hash)
            )
            existing_counts = {bh: int(c) for bh, c in res.all()}

        result = reconcile(records, existing_counts)
        for rec in result.to_insert:
            session.add(
                RealTransaction(
                    kind=rec.kind,
                    shop=rec.shop,
                    machine=rec.machine,
                    raw_name=rec.raw_name,
                    value=rec.value,
                    pay_type=rec.pay_type,
                    occurred_at=rec.occurred_at,
                    occurred_date_raw=rec.occurred_date_raw,
                    occurred_time_raw=rec.occurred_time_raw,
                    base_hash=rec.base_hash,
                    dedup_key=rec.dedup_key,
                    occurrence_index=rec.occurrence_index,
                )
            )
        if result.seen_keys:
            await session.execute(
                update(RealTransaction)
                .where(RealTransaction.dedup_key.in_(result.seen_keys))
                .values(last_seen_at=utcnow())
            )

        run.rows_seen = len(records)
        run.rows_inserted = len(result.to_insert)
        run.status = SyncStatus.OK.value
        run.finished_at = utcnow()
        session.add(run)
        await session.commit()
    except Exception as exc:  # never let the scheduler thread die
        await session.rollback()
        run.status = SyncStatus.PARTIAL.value
        run.error = str(exc)[:500]
        run.finished_at = utcnow()
        session.add(run)
        await session.commit()
    return run
