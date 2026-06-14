"""Synced-history admin page + CSV export (admin-only)."""
from __future__ import annotations

import httpx
import pytest_asyncio
from httpx import ASGITransport
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.db import Base, get_session
from app.main import create_app
from app.models.real import RealTransaction
from app.models.user import Member
from app.services.auth_service import hash_password
from app.util.time import utcnow


@pytest_asyncio.fixture
async def ctx():
    engine = create_async_engine(
        "sqlite+aiosqlite://", connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as c:
        await c.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    application = create_app()

    async def _override():
        async with maker() as s:
            yield s

    application.dependency_overrides[get_session] = _override
    async with maker() as s:
        s.add(Member(email="admin@nekopay.app", display_name="Admin",
                     password_hash=hash_password("secret1"), role="admin"))
        s.add(Member(email="bob@nekopay.app", display_name="Bob",
                     password_hash=hash_password("secret1"), role="member"))
        s.add(RealTransaction(
            kind="pay", shop="竹喵店", machine="Chunithm", raw_name="竹喵店 - Chunithm",
            value=-3, pay_type="point", occurred_at=utcnow(),
            occurred_date_raw="06/10", occurred_time_raw="20:47",
            base_hash="h1", dedup_key="h1", occurrence_index=0))
        await s.commit()
    yield ASGITransport(app=application)
    await engine.dispose()


async def _login(transport, email):
    c = httpx.AsyncClient(transport=transport, base_url="http://t")
    await c.post("/api/auth/login", json={"email": email, "password": "secret1"})
    return c


async def test_admin_can_view_history_and_csv(ctx):
    admin = await _login(ctx, "admin@nekopay.app")
    r = await admin.get("/admin/history")
    assert r.status_code == 200 and "真實交易歷史" in r.text

    csv = await admin.get("/admin/history.csv")
    assert csv.status_code == 200
    assert "text/csv" in csv.headers["content-type"]
    assert "occurred_at,kind,shop" in csv.text
    assert "竹喵店" in csv.text  # the row is exported
    await admin.aclose()


async def test_member_cannot_view_history(ctx):
    bob = await _login(ctx, "bob@nekopay.app")
    assert (await bob.get("/admin/history")).status_code == 303      # redirected away
    assert (await bob.get("/admin/history.csv")).status_code == 303
    await bob.aclose()


async def test_history_filter_by_kind(ctx):
    admin = await _login(ctx, "admin@nekopay.app")
    # only a 'pay' row exists -> filtering topup shows none in the table body
    r = await admin.get("/admin/history", params={"kind": "topup"})
    assert r.status_code == 200 and "竹喵店 - Chunithm" not in r.text
    r = await admin.get("/admin/history", params={"kind": "pay"})
    assert "竹喵店 - Chunithm" in r.text
    await admin.aclose()
