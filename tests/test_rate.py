"""Cash<->point rate is admin-only; members enter points only (NT$ derived)."""
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
from app.models.user import Member
from app.services.auth_service import hash_password


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
        await s.commit()
    yield ASGITransport(app=application)
    await engine.dispose()


async def _login(transport, email):
    c = httpx.AsyncClient(transport=transport, base_url="http://t")
    r = await c.post("/api/auth/login", json={"email": email, "password": "secret1"})
    return c, {"X-CSRF-Token": r.json()["csrf_token"]}


async def _id(c, email):
    return next(m["id"] for m in (await c.get("/api/members")).json()
               if m["email"] == email)


async def test_member_topup_derives_money_from_rate(ctx):
    c, h = await _login(ctx, "bob@nekopay.app")
    bob = await _id(c, "bob@nekopay.app")
    r = await c.post("/api/topups", headers=h, json={"member_id": bob, "points": 100})
    assert r.status_code == 200
    assert r.json()["money_nt"] == "100.00"  # default rate 1.0
    await c.aclose()


async def test_member_cannot_override_money(ctx):
    c, h = await _login(ctx, "bob@nekopay.app")
    bob = await _id(c, "bob@nekopay.app")
    # member tries to sneak a money_nt -> ignored, still points * rate
    r = await c.post("/api/topups", headers=h,
                     json={"member_id": bob, "points": 100, "money_nt": 9999})
    assert r.status_code == 200 and r.json()["money_nt"] == "100.00"
    await c.aclose()


async def test_member_cannot_set_rate(ctx):
    c, h = await _login(ctx, "bob@nekopay.app")
    r = await c.post("/api/admin/rate", headers=h, json={"rate": 5})
    assert r.status_code == 403
    await c.aclose()


async def test_admin_sets_rate_and_member_topup_follows(ctx):
    admin, ah = await _login(ctx, "admin@nekopay.app")
    r = await admin.post("/api/admin/rate", headers=ah, json={"rate": "2"})
    assert r.status_code == 200 and r.json()["rate"] == "2"
    assert (await admin.get("/api/admin/rate")).json()["rate"] == "2"
    await admin.aclose()

    bob, bh = await _login(ctx, "bob@nekopay.app")
    bid = await _id(bob, "bob@nekopay.app")
    r = await bob.post("/api/topups", headers=bh, json={"member_id": bid, "points": 10})
    assert r.json()["money_nt"] == "20.00"  # 10 points * rate 2
    await bob.aclose()


async def test_admin_may_override_money(ctx):
    admin, ah = await _login(ctx, "admin@nekopay.app")
    adm = await _id(admin, "admin@nekopay.app")
    r = await admin.post("/api/topups", headers=ah,
                         json={"member_id": adm, "points": 10, "money_nt": 333})
    assert r.json()["money_nt"] == "333.00"  # admin override honored
    await admin.aclose()
