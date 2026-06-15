"""In-process APScheduler that runs the NekoPay sync cycle.

A single SyncManager instance is stored on app.state and shared by the
scheduled job and the admin "run now" endpoint, so they reuse one HTTP client
and token. Assumes a single app instance (RUN_SCHEDULER gates extra replicas).
"""
from __future__ import annotations

import asyncio
import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

from app.config import Settings
from app.db import SessionLocal
from app.models.real import SyncRun
from app.services.nekopay_client import NekoPayClient
from app.services.sync_service import run_sync_cycle
from app.services.token_manager import TokenManager

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
        # serializes on-demand syncs so rapid clicks don't run overlapping cycles
        self._ondemand_lock = asyncio.Lock()

    async def _run(self, include_snapshot: bool = True) -> SyncRun:
        async with SessionLocal() as session:
            run = await run_sync_cycle(
                session, self.client, self.token_manager, self.settings.app_timezone,
                include_snapshot=include_snapshot,
            )
            log.info(
                "sync cycle: status=%s seen=%s inserted=%s",
                run.status, run.rows_seen, run.rows_inserted,
            )
            return run

    async def run_once(self, include_snapshot: bool = True) -> SyncRun:
        """Manual trigger (admin endpoint)."""
        return await self._run(include_snapshot=include_snapshot)

    async def sync_now_safe(self, *, include_snapshot: bool = True) -> SyncRun | None:
        """Force a sync now, best-effort. Used before auto-attribution matching so
        the member's just-made real transaction always shows up. Serialized by a
        lock so rapid clicks don't overlap. No-op (returns None) if creds are
        unset or the sync fails — never raises into the caller's request."""
        if not (self.settings.nekopay_email and self.settings.nekopay_password):
            return None
        async with self._ondemand_lock:
            try:
                return await self.run_once(include_snapshot=include_snapshot)
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
