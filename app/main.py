"""FastAPI application factory and wiring."""
from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import timedelta

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from slowapi.util import get_remote_address
from sqlalchemy import select

from app.api import (
    actions,
    admin,
    analytics,
    attribution,
    auth,
    ledger,
    members,
)
from app.config import get_settings
from app.db import Base, SessionLocal, engine
from app.models.auth import UserSession
from app.models.user import Member
from app.services.auth_service import hash_password
from app.services.errors import DomainError
from app.sync.scheduler import SyncManager
from app.util.time import utcnow
from app.web import router as web_router

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("nekopay")
settings = get_settings()

limiter = Limiter(key_func=get_remote_address, default_limits=["300/minute"])


async def _bootstrap_admin() -> None:
    if not (settings.admin_bootstrap_username and settings.admin_bootstrap_password):
        return
    async with SessionLocal() as s:
        exists = (
            await s.execute(
                select(Member).where(
                    Member.username == settings.admin_bootstrap_username
                )
            )
        ).scalar_one_or_none()
        if exists:
            return
        s.add(
            Member(
                username=settings.admin_bootstrap_username,
                display_name="Admin",
                password_hash=hash_password(settings.admin_bootstrap_password),
                role="admin",
            )
        )
        await s.commit()
        log.info("bootstrapped admin %s", settings.admin_bootstrap_username)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # dev convenience: ensure tables exist (prod uses alembic)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    await _bootstrap_admin()

    sync_manager = SyncManager(settings)
    app.state.sync_manager = sync_manager
    sync_manager.start()
    # also sync once immediately at startup so the DB is fresh right away
    # (the interval timer's first tick is one interval later)
    if settings.run_scheduler and settings.nekopay_email and settings.nekopay_password:
        app.state._startup_sync = asyncio.create_task(_startup_sync(sync_manager))
    try:
        yield
    finally:
        await sync_manager.shutdown()


async def _startup_sync(sync_manager: SyncManager) -> None:
    try:
        await sync_manager.run_once()
    except Exception:  # never let a startup sync hiccup crash the app
        log.warning("startup sync failed", exc_info=True)


def create_app() -> FastAPI:
    app = FastAPI(title="NekoPay Ledger", version="0.1.0", lifespan=lifespan)
    app.state.limiter = limiter
    app.add_middleware(SlowAPIMiddleware)

    @app.exception_handler(DomainError)
    async def _domain_error(_request: Request, exc: DomainError):
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})

    @app.exception_handler(RateLimitExceeded)
    async def _rate_limited(_request: Request, _exc: RateLimitExceeded):
        return JSONResponse(status_code=429, content={"detail": "rate limit exceeded"})

    @app.middleware("http")
    async def _security_headers(request: Request, call_next):
        resp = await call_next(request)
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["X-Frame-Options"] = "DENY"
        resp.headers["Referrer-Policy"] = "same-origin"
        resp.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self' 'unsafe-inline'; "
            "style-src 'self' 'unsafe-inline'; img-src 'self' data:"
        )
        if settings.is_prod:
            resp.headers["Strict-Transport-Security"] = (
                "max-age=31536000; includeSubDomains"
            )
        return resp

    @app.middleware("http")
    async def _slide_session(request: Request, call_next):
        """Sliding session: on activity, extend a valid session + re-set the
        cookie so an active user effectively logs in only once. Throttled to at
        most once/day per session; never breaks the response if it fails."""
        response = await call_next(request)
        path = request.url.path
        if path == "/healthz" or path.startswith("/static"):
            return response
        token = request.cookies.get(settings.session_cookie_name)
        if not token:
            return response
        try:
            async with SessionLocal() as s:
                us = await s.get(UserSession, token)
                now = utcnow()
                if (
                    us is not None
                    and us.expires_at > now
                    and (now - us.last_seen_at).total_seconds() > 86400
                ):
                    us.last_seen_at = now
                    us.expires_at = now + timedelta(hours=settings.session_ttl_hours)
                    await s.commit()
                    response.set_cookie(
                        key=settings.session_cookie_name,
                        value=token,
                        max_age=settings.session_ttl_hours * 3600,
                        httponly=True,
                        secure=settings.cookies_secure,
                        samesite="lax",
                    )
        except Exception:  # never fail a response over session renewal
            log.warning("session slide failed", exc_info=True)
        return response

    @app.get("/healthz", tags=["health"])
    async def healthz():
        return {"status": "ok"}

    app.include_router(auth.router)
    app.include_router(members.router)
    app.include_router(actions.router)
    app.include_router(ledger.router)
    app.include_router(admin.router)
    app.include_router(analytics.router)
    app.include_router(attribution.router)
    app.include_router(web_router)

    app.mount("/static", StaticFiles(directory="app/static"), name="static")
    return app


app = create_app()
