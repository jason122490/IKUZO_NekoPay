"""In-process APScheduler that runs the NekoPay sync cycle.

A single SyncManager instance is stored on app.state and shared by the
scheduled job and the admin "run now" endpoint, so they reuse one HTTP client
and token. Assumes a single app instance (RUN_SCHEDULER gates extra replicas).
"""
from __future__ import annotations

import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.db import SessionLocal
from app.models.real import AccountSnapshot, SyncRun
from app.services.nekopay_client import NekoPayClient
from app.services.sync_service import run_sync_cycle
from app.services.token_manager import TokenManager
from app.util.time import utcnow

log = logging.getLogger("nekopay.sync")


class SyncManager:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.client = NekoPayClient(
            settings.nekopay_base_url,
            settings.nekopay_user_agent,
            proxy=settings.nekopay_proxy,
        )
        self.token_manager = TokenManager(
            self.client, settings.nekopay_email, settings.nekopay_password
        )
        self._scheduler = AsyncIOScheduler()

    async def _run(self) -> SyncRun:
        async with SessionLocal() as session:
            run = await run_sync_cycle(
                session, self.client, self.token_manager, self.settings.app_timezone
            )
            log.info(
                "sync cycle: status=%s seen=%s inserted=%s",
                run.status, run.rows_seen, run.rows_inserted,
            )
            return run

    async def run_once(self) -> SyncRun:
        """Manual trigger (admin endpoint)."""
        return await self._run()

    async def run_if_stale(
        self, session: AsyncSession, max_age_sec: int = 20
    ) -> SyncRun | None:
        """Sync the real account on demand, unless a snapshot is recent enough.

        Used before auto-attribution matching so the member's just-made real
        transaction shows up. No-op (returns None) if creds are unset, data is
        fresh, or the sync fails (best-effort)."""
        if not (self.settings.nekopay_email and self.settings.nekopay_password):
            return None
        snap = (
            await session.execute(
                select(AccountSnapshot)
                .order_by(AccountSnapshot.captured_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        if snap is not None and (utcnow() - snap.captured_at).total_seconds() < max_age_sec:
            return None
        try:
            return await self.run_once()
        except Exception:  # never fail the caller's request because sync hiccuped
            log.warning("on-demand sync failed", exc_info=True)
            return None

    def start(self) -> None:
        if not self.settings.run_scheduler:
            log.info("scheduler disabled (RUN_SCHEDULER=false)")
            return
        if not self.settings.nekopay_email or not self.settings.nekopay_password:
            log.warning("NEKOPAY credentials not set; scheduler not started")
            return
        self._scheduler.add_job(
            self._run,
            IntervalTrigger(seconds=self.settings.sync_interval_seconds, jitter=20),
            id="nekopay_sync",
            max_instances=1,
            coalesce=True,
            misfire_grace_time=120,
            replace_existing=True,
        )
        self._scheduler.start()
        log.info("scheduler started: every %ss", self.settings.sync_interval_seconds)

    async def shutdown(self) -> None:
        if self._scheduler.running:
            self._scheduler.shutdown(wait=False)
        await self.client.aclose()
