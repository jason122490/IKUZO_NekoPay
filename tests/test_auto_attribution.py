"""Auto-attribution: same-amount matching, self-attribution, money override,
toggle persistence, and conflict handling."""
from __future__ import annotations

import httpx
import pytest_asyncio
from httpx import ASGITransport
from sqlalchemy import select
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


def _rt(kind, value, name, key):
    return RealTransaction(
        kind=kind, shop=name.split(" - ")[0],
        machine=(name.split(" - ")[1] if " - " in name else None),
        raw_name=name, value=value, pay_type=("point" if kind == "pay" else None),
        occurred_at=utcnow(), occurred_date_raw="06/10", occurred_time_raw="20:47",
        base_hash=key, dedup_key=key, occurrence_index=0,
    )


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
        s.add_all([
            _rt("pay", -3, "竹喵店 - Chunithm", "k1"),
            _rt("pay", -3, "竹喵店 - Chunithm", "k2"),
            _rt("pay", -5, "竹喵店 - maimaiDX", "k3"),
            _rt("topup", 33, "竹喵店", "k4"),
        ])
        await s.commit()
    yield ASGITransport(app=application), maker
    await engine.dispose()


async def _login(transport, email="bob@nekopay.app", password="secret1"):
    c = httpx.AsyncClient(transport=transport, base_url="http://t")
    r = await c.post("/api/auth/login", json={"email": email, "password": password})
    assert r.status_code == 200, r.text
    return c, {"X-CSRF-Token": r.json()["csrf_token"]}


async def test_match_only_same_amount(ctx):
    transport, _ = ctx
    c, h = await _login(transport)
    r = await c.post("/api/attribution/match", headers=h, json={"kind": "pay", "points": 3})
    assert r.status_code == 200
    assert len(r.json()["candidates"]) == 2  # two -3 pays, not the -5
    r = await c.post("/api/attribution/match", headers=h, json={"kind": "pay", "points": 5})
    assert len(r.json()["candidates"]) == 1
    r = await c.post("/api/attribution/match", headers=h, json={"kind": "topup", "points": 33})
    assert len(r.json()["candidates"]) == 1
    await c.aclose()


async def test_self_attribute_links_and_consumes(ctx):
    transport, maker = ctx
    c, h = await _login(transport)
    cand = (await c.post("/api/attribution/match", headers=h,
                         json={"kind": "pay", "points": 3})).json()["candidates"]
    rid = cand[0]["id"]
    r = await c.post(f"/api/attribution/self/{rid}", headers=h, json={})
    assert r.status_code == 200
    entry = r.json()
    assert entry["entry_type"] == "PLAY" and entry["points_delta"] == -3
    assert entry["source_real_txn_id"] == rid

    # that real txn is now attributed -> only one -3 candidate remains
    left = (await c.post("/api/attribution/match", headers=h,
                         json={"kind": "pay", "points": 3})).json()["candidates"]
    assert len(left) == 1

    async with maker() as s:
        rt = await s.get(RealTransaction, rid)
        assert rt.attribution_status == "attributed"
    await c.aclose()


async def test_self_attribute_topup_uses_member_money(ctx):
    # points come from the matched real txn; money is what the member paid
    transport, _ = ctx
    c, h = await _login(transport)
    bob = next(m["id"] for m in (await c.get("/api/members")).json()
               if m["email"] == "bob@nekopay.app")
    cand = (await c.post("/api/attribution/match", headers=h,
                         json={"kind": "topup", "points": 33})).json()["candidates"][0]
    r = await c.post(f"/api/attribution/self/{cand['id']}", headers=h,
                     json={"money_nt": "50"})
    assert r.status_code == 200
    assert r.json()["money_nt"] == "50.00"          # member's reported cash
    bal = (await c.get(f"/api/members/{bob}/balance")).json()
    assert bal["money_contributed"] == "50.00" and bal["points_balance"] == 33
    await c.aclose()


async def test_toggle_auto_attribute_persists(ctx):
    transport, _ = ctx
    c, h = await _login(transport)
    assert (await c.get("/api/auth/me")).json()["auto_attribute"] is True  # default on
    r = await c.post("/api/auth/auto-attribute", headers=h, json={"enabled": False})
    assert r.status_code == 200 and r.json()["auto_attribute"] is False
    assert (await c.get("/api/auth/me")).json()["auto_attribute"] is False
    await c.aclose()


async def test_self_attribute_conflict_when_already_taken(ctx):
    transport, _ = ctx
    bob_c, bh = await _login(transport, "bob@nekopay.app")
    cand = (await bob_c.post("/api/attribution/match", headers=bh,
                             json={"kind": "pay", "points": 5})).json()["candidates"][0]
    assert (await bob_c.post(f"/api/attribution/self/{cand['id']}", headers=bh,
                             json={})).status_code == 200
    # admin tries to grab the same one -> already attributed
    admin_c, ah = await _login(transport, "admin@nekopay.app")
    r = await admin_c.post(f"/api/attribution/self/{cand['id']}", headers=ah, json={})
    assert r.status_code == 409
    await bob_c.aclose(); await admin_c.aclose()
