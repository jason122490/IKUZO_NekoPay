"""Dashboard renders and lays sections out in the requested order."""
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
        s.add(Member(username="admin", display_name="Admin",
                     password_hash=hash_password("secret1"), role="admin"))
        s.add(Member(username="bob", display_name="Bob",
                     password_hash=hash_password("secret1"), role="member"))
        await s.commit()
    yield ASGITransport(app=application)
    await engine.dispose()


async def _login(transport, username):
    c = httpx.AsyncClient(transport=transport, base_url="http://t")
    await c.post("/api/auth/login", json={"username": username, "password": "secret1"})
    return c


# requested layout: 餘額/總消費 -> 投幣 -> 儲值 -> 轉點 -> 近期紀錄 -> 自動歸戶 -> 成員餘額
ORDER = ["我的餘額 / 總消費", "投幣", "儲值", "轉點",
         "我的近期紀錄", "自動歸戶", "成員餘額與分帳"]


def _assert_order(html: str):
    positions = [html.find(h) for h in ORDER]
    assert all(p >= 0 for p in positions), positions  # every section present
    assert positions == sorted(positions), positions  # and in this order


async def test_member_dashboard_section_order(ctx):
    bob = await _login(ctx, "bob")
    r = await bob.get("/dashboard")
    assert r.status_code == 200
    _assert_order(r.text)
    await bob.aclose()


async def test_admin_dashboard_renders_with_recon(ctx):
    admin = await _login(ctx, "admin")
    r = await admin.get("/dashboard")
    assert r.status_code == 200
    _assert_order(r.text)
    # admin-only reconciliation card sits between 自動歸戶 and 成員餘額
    assert r.text.find("對帳（管理員）") < r.text.find("成員餘額與分帳")
    await admin.aclose()
