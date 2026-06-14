"""Admin member-management tests: create UI flow, edit, role, disable, reset,
plus self-protection and role enforcement."""
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
        s.add(Member(username="admin@nekopay.app", display_name="Admin",
                     password_hash=hash_password("secret1"), role="admin"))
        s.add(Member(username="bob@nekopay.app", display_name="Bob",
                     password_hash=hash_password("secret1"), role="member"))
        await s.commit()
    yield ASGITransport(app=application)
    await engine.dispose()


async def _login(transport, username, password):
    c = httpx.AsyncClient(transport=transport, base_url="http://t")
    r = await c.post("/api/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    return c, r.json()["csrf_token"]


async def _id(client, username):
    members = (await client.get("/api/members")).json()
    return next(m["id"] for m in members if m["username"] == username)


async def test_admin_creates_account(ctx):
    admin, csrf = await _login(ctx, "admin@nekopay.app", "secret1")
    h = {"X-CSRF-Token": csrf}
    r = await admin.post("/api/members", headers=h, json={
        "username": "carol@nekopay.app", "display_name": "Carol",
        "password": "secret1", "role": "member"})
    assert r.status_code == 200 and r.json()["display_name"] == "Carol"
    # duplicate username -> 409
    r = await admin.post("/api/members", headers=h, json={
        "username": "carol@nekopay.app", "display_name": "Dup", "password": "secret1"})
    assert r.status_code == 409
    await admin.aclose()


async def test_cannot_create_duplicate_display_name(ctx):
    admin, csrf = await _login(ctx, "admin@nekopay.app", "secret1")
    h = {"X-CSRF-Token": csrf}
    # "Bob" already exists -> duplicate name (case-insensitive) rejected
    r = await admin.post("/api/members", headers=h, json={
        "username": "bob2@nekopay.app", "display_name": "bob", "password": "secret1"})
    assert r.status_code == 409
    await admin.aclose()


async def test_cannot_rename_to_existing_name(ctx):
    admin, csrf = await _login(ctx, "admin@nekopay.app", "secret1")
    h = {"X-CSRF-Token": csrf}
    bob = await _id(admin, "bob@nekopay.app")
    # rename Bob -> "Admin" (taken) -> 409
    r = await admin.post(f"/api/members/{bob}/update", headers=h,
                         json={"display_name": "Admin"})
    assert r.status_code == 409
    # renaming to your own current name is fine (excludes self)
    r2 = await admin.post(f"/api/members/{bob}/update", headers=h,
                          json={"display_name": "Bob"})
    assert r2.status_code == 200
    await admin.aclose()


async def test_admin_edit_role_and_rename(ctx):
    admin, csrf = await _login(ctx, "admin@nekopay.app", "secret1")
    h = {"X-CSRF-Token": csrf}
    bob = await _id(admin, "bob@nekopay.app")
    r = await admin.post(f"/api/members/{bob}/update", headers=h,
                         json={"display_name": "Bobby", "role": "admin"})
    assert r.status_code == 200
    assert r.json()["display_name"] == "Bobby" and r.json()["role"] == "admin"
    await admin.aclose()


async def test_admin_cannot_change_own_role(ctx):
    admin, csrf = await _login(ctx, "admin@nekopay.app", "secret1")
    me = await _id(admin, "admin@nekopay.app")
    r = await admin.post(f"/api/members/{me}/update",
                         headers={"X-CSRF-Token": csrf}, json={"role": "member"})
    assert r.status_code == 400
    await admin.aclose()


async def test_admin_cannot_disable_self(ctx):
    admin, csrf = await _login(ctx, "admin@nekopay.app", "secret1")
    me = await _id(admin, "admin@nekopay.app")
    r = await admin.post(f"/api/members/{me}/status",
                         headers={"X-CSRF-Token": csrf}, json={"is_active": False})
    assert r.status_code == 400
    await admin.aclose()


async def test_disable_revokes_sessions_and_blocks_login(ctx):
    admin, csrf = await _login(ctx, "admin@nekopay.app", "secret1")
    bob = await _id(admin, "bob@nekopay.app")
    # bob logs in and is active
    bob_c, _ = await _login(ctx, "bob@nekopay.app", "secret1")
    assert (await bob_c.get("/api/auth/me")).status_code == 200
    # admin disables bob
    r = await admin.post(f"/api/members/{bob}/status",
                         headers={"X-CSRF-Token": csrf}, json={"is_active": False})
    assert r.status_code == 200
    # bob's existing session is revoked
    assert (await bob_c.get("/api/auth/me")).status_code == 401
    # and he can no longer log in
    fresh = httpx.AsyncClient(transport=ctx, base_url="http://t")
    assert (await fresh.post("/api/auth/login",
            json={"username": "bob@nekopay.app", "password": "secret1"})).status_code == 401
    await admin.aclose(); await bob_c.aclose(); await fresh.aclose()


async def test_admin_reset_password(ctx):
    admin, csrf = await _login(ctx, "admin@nekopay.app", "secret1")
    bob = await _id(admin, "bob@nekopay.app")
    r = await admin.post(f"/api/members/{bob}/reset-password",
                         headers={"X-CSRF-Token": csrf}, json={"new_password": "newpass1"})
    assert r.status_code == 200
    # old password fails, new works
    c1 = httpx.AsyncClient(transport=ctx, base_url="http://t")
    assert (await c1.post("/api/auth/login",
            json={"username": "bob@nekopay.app", "password": "secret1"})).status_code == 401
    assert (await c1.post("/api/auth/login",
            json={"username": "bob@nekopay.app", "password": "newpass1"})).status_code == 200
    await admin.aclose(); await c1.aclose()


async def test_delete_account_with_no_history(ctx):
    admin, csrf = await _login(ctx, "admin@nekopay.app", "secret1")
    h = {"X-CSRF-Token": csrf}
    # create a fresh account with no transactions, then delete it
    await admin.post("/api/members", headers=h, json={
        "username": "temp@nekopay.app", "display_name": "Temp", "password": "secret1"})
    temp = await _id(admin, "temp@nekopay.app")
    r = await admin.request("DELETE", f"/api/members/{temp}", headers=h)
    assert r.status_code == 200, r.text
    # gone from the list
    emails = [m["username"] for m in (await admin.get("/api/members")).json()]
    assert "temp@nekopay.app" not in emails
    await admin.aclose()


async def test_delete_blocked_when_member_has_history(ctx):
    admin, csrf = await _login(ctx, "admin@nekopay.app", "secret1")
    h = {"X-CSRF-Token": csrf}
    bob = await _id(admin, "bob@nekopay.app")
    await admin.post("/api/topups", headers=h,
                     json={"member_id": bob, "money_nt": 500})
    r = await admin.request("DELETE", f"/api/members/{bob}", headers=h)
    assert r.status_code == 409  # has ledger history -> must disable instead
    await admin.aclose()


async def test_cannot_delete_self(ctx):
    admin, csrf = await _login(ctx, "admin@nekopay.app", "secret1")
    me = await _id(admin, "admin@nekopay.app")
    r = await admin.request("DELETE", f"/api/members/{me}", headers={"X-CSRF-Token": csrf})
    assert r.status_code == 400
    await admin.aclose()


async def test_member_cannot_delete_accounts(ctx):
    bob_c, csrf = await _login(ctx, "bob@nekopay.app", "secret1")
    r = await bob_c.request("DELETE", "/api/members/1", headers={"X-CSRF-Token": csrf})
    assert r.status_code == 403
    await bob_c.aclose()


async def test_member_cannot_manage_accounts(ctx):
    bob_c, csrf = await _login(ctx, "bob@nekopay.app", "secret1")
    admin_id = 1
    for path, body in [
        (f"/api/members/{admin_id}/update", {"display_name": "X"}),
        (f"/api/members/{admin_id}/status", {"is_active": False}),
        (f"/api/members/{admin_id}/reset-password", {"new_password": "hacked1"}),
    ]:
        r = await bob_c.post(path, headers={"X-CSRF-Token": csrf}, json=body)
        assert r.status_code == 403, path
    await bob_c.aclose()
