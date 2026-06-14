"""FastAPI application factory and wiring."""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from slowapi.util import get_remote_address
from sqlalchemy import select

from app.api import actions, admin, analytics, attribution, auth, members
from app.config import get_settings
from app.db import Base, SessionLocal, engine
from app.models.user import Member
from app.services.auth_service import hash_password
from app.services.errors import DomainError
from app.sync.scheduler import SyncManager
from app.web import router as web_router

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("nekopay")
settings = get_settings()

limiter = Limiter(key_func=get_remote_address, default_limits=["300/minute"])


async def _bootstrap_admin() -> None:
    if not (settings.admin_bootstrap_email and settings.admin_bootstrap_password):
        return
    async with SessionLocal() as s:
        exists = (
            await s.execute(
                select(Member).where(
                    Member.email == settings.admin_bootstrap_email.lower()
                )
            )
        ).scalar_one_or_none()
        if exists:
            return
        s.add(
            Member(
                email=settings.admin_bootstrap_email.lower(),
                display_name="Admin",
                password_hash=hash_password(settings.admin_bootstrap_password),
                role="admin",
            )
        )
        await s.commit()
        log.info("bootstrapped admin %s", settings.admin_bootstrap_email)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # dev convenience: ensure tables exist (prod uses alembic)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    await _bootstrap_admin()

    sync_manager = SyncManager(settings)
    app.state.sync_manager = sync_manager
    sync_manager.start()
    try:
        yield
    finally:
        await sync_manager.shutdown()


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

    @app.get("/healthz", tags=["health"])
    async def healthz():
        return {"status": "ok"}

    app.include_router(auth.router)
    app.include_router(members.router)
    app.include_router(actions.router)
    app.include_router(admin.router)
    app.include_router(analytics.router)
    app.include_router(attribution.router)
    app.include_router(web_router)

    app.mount("/static", StaticFiles(directory="app/static"), name="static")
    return app


app = create_app()
