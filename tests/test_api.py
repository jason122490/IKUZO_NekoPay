"""End-to-end API tests (httpx ASGITransport) incl. auth, CSRF, IDOR."""
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
        "sqlite+aiosqlite://",
        connect_args={"check_same_thread": False},
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
        s.add(
            Member(
                username="admin@nekopay.app",
                display_name="Admin",
                password_hash=hash_password("secret1"),
                role="admin",
            )
        )
        s.add(
            Member(
                username="bob@nekopay.app",
                display_name="Bob",
                password_hash=hash_password("secret1"),
                role="member",
            )
        )
        await s.commit()

    yield ASGITransport(app=application)
    await engine.dispose()


async def _login(transport, username, password):
    c = httpx.AsyncClient(transport=transport, base_url="http://t")
    r = await c.post("/api/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    return c, r.json()["csrf_token"]


async def _member_id(client, username):
    members = (await client.get("/api/members")).json()
    return next(m["id"] for m in members if m["username"] == username)


async def test_login_me_and_unauth(ctx):
    c, _ = await _login(ctx, "admin@nekopay.app", "secret1")
    r = await c.get("/api/auth/me")
    assert r.status_code == 200 and r.json()["role"] == "admin"
    await c.aclose()

    anon = httpx.AsyncClient(transport=ctx, base_url="http://t")
    assert (await anon.get("/api/auth/me")).status_code == 401
    await anon.aclose()


async def test_csrf_required_on_mutations(ctx):
    c, _ = await _login(ctx, "admin@nekopay.app", "secret1")
    bob = await _member_id(c, "bob@nekopay.app")
    # no CSRF header -> blocked
    r = await c.post("/api/topups", json={"member_id": bob, "money_nt": 100})
    assert r.status_code == 403
    await c.aclose()


async def test_four_actions_flow(ctx):
    admin, csrf = await _login(ctx, "admin@nekopay.app", "secret1")
    h = {"X-CSRF-Token": csrf}
    bob = await _member_id(admin, "bob@nekopay.app")
    adm = await _member_id(admin, "admin@nekopay.app")

    # create a new member (創建帳號)
    r = await admin.post(
        "/api/members",
        json={"username": "carol@nekopay.app", "display_name": "Carol", "password": "secret1"},
        headers=h,
    )
    assert r.status_code == 200, r.text

    # 儲值 (money input; rate 10, no snapshot -> 1000 NT$ = 100 點)
    r = await admin.post(
        "/api/topups", json={"member_id": bob, "money_nt": 1000}, headers=h
    )
    assert r.status_code == 200
    bal = (await admin.get(f"/api/members/{bob}/balance")).json()
    assert bal["points_balance"] == 100

    # 投幣
    r = await admin.post(
        "/api/plays", json={"member_id": bob, "points": 30}, headers=h
    )
    assert r.status_code == 200

    # 投幣 overdraft blocked (409)
    r = await admin.post(
        "/api/plays", json={"member_id": bob, "points": 9999}, headers=h
    )
    assert r.status_code == 409

    # 轉點 bob -> admin
    r = await admin.post(
        "/api/transfers",
        json={"from_member_id": bob, "to_member_id": adm, "points": 20},
        headers=h,
    )
    assert r.status_code == 200
    assert (await admin.get(f"/api/members/{bob}/balance")).json()["points_balance"] == 50
    assert (await admin.get(f"/api/members/{adm}/balance")).json()["points_balance"] == 20
    await admin.aclose()


async def test_member_cannot_read_other_ledger_idor(ctx):
    admin, _ = await _login(ctx, "admin@nekopay.app", "secret1")
    adm = await _member_id(admin, "admin@nekopay.app")
    await admin.aclose()

    bob_c, _ = await _login(ctx, "bob@nekopay.app", "secret1")
    r = await bob_c.get(f"/api/members/{adm}/ledger")
    assert r.status_code == 403  # member may not read another member's ledger
    await bob_c.aclose()


async def test_login_sets_persistent_httponly_cookie(ctx):
    c = httpx.AsyncClient(transport=ctx, base_url="http://t")
    r = await c.post("/api/auth/login",
                     json={"username": "admin@nekopay.app", "password": "secret1"})
    assert r.status_code == 200
    set_cookie = "; ".join(r.headers.get_list("set-cookie")).lower()
    assert "max-age=" in set_cookie  # persistent (survives browser restart)
    assert "httponly" in set_cookie  # not readable by JS
    await c.aclose()


async def test_cookie_auto_login_without_credentials(ctx):
    # log in once, capture the cookie
    c1 = httpx.AsyncClient(transport=ctx, base_url="http://t")
    r = await c1.post("/api/auth/login",
                      json={"username": "admin@nekopay.app", "password": "secret1"})
    token = r.cookies.get("nekopay_session")
    assert token
    await c1.aclose()
    # a fresh client carrying ONLY the cookie is auto-authenticated (no creds)
    c2 = httpx.AsyncClient(transport=ctx, base_url="http://t",
                           cookies={"nekopay_session": token})
    assert (await c2.get("/api/auth/me")).status_code == 200
    await c2.aclose()


async def test_member_cannot_act_for_others(ctx):
    bob_c, csrf = await _login(ctx, "bob@nekopay.app", "secret1")
    h = {"X-CSRF-Token": csrf}
    adm = await _member_id(bob_c, "admin@nekopay.app")
    # top up / play for someone else -> 403
    assert (await bob_c.post("/api/topups", headers=h,
            json={"member_id": adm, "money_nt": 100})).status_code == 403
    assert (await bob_c.post("/api/plays", headers=h,
            json={"member_id": adm, "points": 10})).status_code == 403
    # transfer FROM someone else -> 403
    bob = await _member_id(bob_c, "bob@nekopay.app")
    assert (await bob_c.post("/api/transfers", headers=h,
            json={"from_member_id": adm, "to_member_id": bob, "points": 5})).status_code == 403
    await bob_c.aclose()


async def test_member_cannot_create_member(ctx):
    bob_c, csrf = await _login(ctx, "bob@nekopay.app", "secret1")
    r = await bob_c.post(
        "/api/members",
        json={"username": "x@nekopay.app", "display_name": "X", "password": "secret1"},
        headers={"X-CSRF-Token": csrf},
    )
    assert r.status_code == 403  # admin only
    await bob_c.aclose()
