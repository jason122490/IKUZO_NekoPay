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
# match on section headers (the rate note also contains "儲值", so substrings
# alone are ambiguous)
ORDER = ["<h2>我的餘額 / 總消費", "<h2>投幣", "<h2>儲值", "<h2>轉點",
         "<h2>我的近期紀錄", 'id="aa_toggle"', "<h2>成員餘額"]


def _assert_order(html: str):
    positions = [html.find(h) for h in ORDER]
    assert all(p >= 0 for p in positions), positions  # every section present
    assert positions == sorted(positions), positions  # and in this order


async def test_member_dashboard_section_order(ctx):
    bob = await _login(ctx, "bob")
    r = await bob.get("/dashboard")
    assert r.status_code == 200
    _assert_order(r.text)
    # site-wide footer is present
    assert "Created by Claude Code with Opus 4.8" in r.text
    assert "jason122490@gmail.com" in r.text
    # nav shows the localized role after the nickname
    assert "一般會員" in r.text
    await bob.aclose()


async def test_dashboard_uses_personal_rate_for_nt(ctx):
    c = httpx.AsyncClient(transport=ctx, base_url="http://t")
    r = await c.post("/api/auth/login", json={"username": "bob", "password": "secret1"})
    h = {"X-CSRF-Token": r.json()["csrf_token"]}
    bob = next(m["id"] for m in (await c.get("/api/members")).json()
               if m["username"] == "bob")
    # pay NT$305 for 30 points -> personal rate 10.17; balance worth 305, not 300
    await c.post("/api/topups", headers=h, json={"member_id": bob, "money_nt": 305})
    page = (await c.get("/dashboard")).text
    assert "依你的平均儲值匯率 10.17 元/點" in page
    assert "NT$ 305" in page
    assert "NT$ 300" not in page  # would be the global-rate value
    await c.aclose()


async def test_records_page_shows_all_and_filters(ctx):
    c = httpx.AsyncClient(transport=ctx, base_url="http://t")
    r = await c.post("/api/auth/login", json={"username": "bob", "password": "secret1"})
    h = {"X-CSRF-Token": r.json()["csrf_token"]}
    bob = next(m["id"] for m in (await c.get("/api/members")).json()
               if m["username"] == "bob")
    for _ in range(18):
        await c.post("/api/topups", headers=h, json={"member_id": bob, "money_nt": 100})
    await c.post("/api/plays", headers=h, json={"member_id": bob, "points": 3})

    def n_rows(text):
        return text.count('onclick="delEntry(')

    # dashboard stays capped at 15; the full page shows everything (18 + 1)
    assert n_rows((await c.get("/dashboard")).text) == 15
    assert n_rows((await c.get("/records")).text) == 19
    # filter by 類型
    assert n_rows((await c.get("/records", params={"kind": "play"})).text) == 1
    assert n_rows((await c.get("/records", params={"kind": "topup"})).text) == 18
    # filter by 歸戶 (none are attributed)
    assert n_rows((await c.get("/records", params={"attr": "no"})).text) == 19
    assert n_rows((await c.get("/records", params={"attr": "yes"})).text) == 0
    # sort options are accepted
    for s in ("time", "type", "attributed"):
        assert (await c.get("/records", params={"sort": s})).status_code == 200
    await c.aclose()


async def test_records_date_range_filter(ctx):
    c = httpx.AsyncClient(transport=ctx, base_url="http://t")
    r = await c.post("/api/auth/login", json={"username": "bob", "password": "secret1"})
    h = {"X-CSRF-Token": r.json()["csrf_token"]}
    bob = next(m["id"] for m in (await c.get("/api/members")).json()
               if m["username"] == "bob")
    for day in ("2026-06-10", "2026-06-11", "2026-06-12"):
        await c.post("/api/topups", headers=h,
                     json={"member_id": bob, "money_nt": 100, "occurred_at": f"{day}T12:00"})

    def n_rows(text):
        return text.count('onclick="delEntry(')

    assert n_rows((await c.get("/records")).text) == 3
    # ranges are inclusive of the local end day (Taipei -> UTC bounds)
    assert n_rows((await c.get("/records", params={"start": "2026-06-11"})).text) == 2
    assert n_rows((await c.get("/records", params={"end": "2026-06-11"})).text) == 2
    assert n_rows((await c.get(
        "/records", params={"start": "2026-06-11", "end": "2026-06-11"})).text) == 1
    await c.aclose()


async def test_footer_on_login_page(ctx):
    c = httpx.AsyncClient(transport=ctx, base_url="http://t")
    r = await c.get("/login")
    assert r.status_code == 200
    assert "Created by Claude Code with Opus 4.8" in r.text
    assert "jason122490@gmail.com" in r.text
    await c.aclose()


async def test_admin_dashboard_renders_with_recon(ctx):
    admin = await _login(ctx, "admin")
    r = await admin.get("/dashboard")
    assert r.status_code == 200
    _assert_order(r.text)
    # admin-only reconciliation card sits between 自動歸戶 and 成員餘額
    assert r.text.find("<h2>對帳（管理員）") < r.text.find("<h2>成員餘額")
    # settlement suggestions removed (no debt scenario)
    assert "結算建議" not in r.text
    # nav shows the localized admin role
    assert "管理員" in r.text
    await admin.aclose()
