"""In-process APScheduler that runs the NekoPay sync cycle.

A single SyncManager instance is stored on app.state and shared by the
scheduled job and the admin "run now" endpoint, so they reuse one HTTP client
and token. Assumes a single app instance (RUN_SCHEDULER gates extra replicas).
"""
from __future__ import annotations

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
            settings.nekopay_base_url, settings.nekopay_user_agent
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
