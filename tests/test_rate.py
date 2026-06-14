"""Rate is admin-only; 儲值 takes money and derives points (+ VIP bonus)."""
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
from app.models.real import AccountSnapshot
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
        s.add(Member(username="admin@nekopay.app", display_name="Admin",
                     password_hash=hash_password("secret1"), role="admin"))
        s.add(Member(username="bob@nekopay.app", display_name="Bob",
                     password_hash=hash_password("secret1"), role="member"))
        await s.commit()
    yield ASGITransport(app=application), maker
    await engine.dispose()


async def _login(transport, username):
    c = httpx.AsyncClient(transport=transport, base_url="http://t")
    r = await c.post("/api/auth/login", json={"username": username, "password": "secret1"})
    return c, {"X-CSRF-Token": r.json()["csrf_token"]}


async def _id(c, username):
    return next(m["id"] for m in (await c.get("/api/members")).json()
               if m["username"] == username)


async def _seed_tier(maker, vip_name):
    async with maker() as s:
        s.add(AccountSnapshot(balance=0, vip_name=vip_name,
                              vip_next_value=27000, is_premium=True))
        await s.commit()


async def test_topup_money_to_points_default_rate(ctx):
    transport, _ = ctx
    c, h = await _login(transport, "bob@nekopay.app")
    bob = await _id(c, "bob@nekopay.app")
    # default rate 10 -> NT$1000 = 100 點 (no snapshot -> no VIP bonus)
    r = await c.post("/api/topups", headers=h, json={"member_id": bob, "money_nt": 1000})
    assert r.status_code == 200
    assert r.json()["points_delta"] == 100
    assert r.json()["money_nt"] == "1000.00"
    await c.aclose()


async def test_member_cannot_set_rate(ctx):
    transport, _ = ctx
    c, h = await _login(transport, "bob@nekopay.app")
    assert (await c.post("/api/admin/rate", headers=h,
                         json={"rate": 5})).status_code == 403
    await c.aclose()


async def test_admin_sets_rate_changes_points(ctx):
    transport, _ = ctx
    admin, ah = await _login(transport, "admin@nekopay.app")
    assert (await admin.post("/api/admin/rate", headers=ah,
                             json={"rate": "20"})).status_code == 200
    await admin.aclose()
    bob, bh = await _login(transport, "bob@nekopay.app")
    bid = await _id(bob, "bob@nekopay.app")
    r = await bob.post("/api/topups", headers=bh, json={"member_id": bid, "money_nt": 100})
    assert r.json()["points_delta"] == 5  # 100 / 20
    await bob.aclose()


async def test_vip_bonus_applies_over_threshold(ctx):
    transport, maker = ctx
    await _seed_tier(maker, "金喵")  # 15% bonus
    c, h = await _login(transport, "bob@nekopay.app")
    bob = await _id(c, "bob@nekopay.app")
    # NT$3000, rate 10 -> base 300; >= 300 -> +15% (45) -> 345
    r = await c.post("/api/topups", headers=h, json={"member_id": bob, "money_nt": 3000})
    assert r.json()["points_delta"] == 345
    await c.aclose()


async def test_vip_bonus_not_applied_below_threshold(ctx):
    transport, maker = ctx
    await _seed_tier(maker, "金喵")
    c, h = await _login(transport, "bob@nekopay.app")
    bob = await _id(c, "bob@nekopay.app")
    # NT$100 < 300 -> no bonus -> 10 點
    r = await c.post("/api/topups", headers=h, json={"member_id": bob, "money_nt": 100})
    assert r.json()["points_delta"] == 10
    await c.aclose()
