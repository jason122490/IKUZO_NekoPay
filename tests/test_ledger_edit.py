"""Edit/delete records: ownership, 30-min window, admin override, side effects."""
from __future__ import annotations

from datetime import timedelta

import httpx
import pytest_asyncio
from httpx import ASGITransport
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.db import Base, get_session
from app.main import create_app
from app.models.ledger import LedgerEntry
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
        await s.commit()
    yield ASGITransport(app=application), maker
    await engine.dispose()


async def _login(transport, email="bob@nekopay.app"):
    c = httpx.AsyncClient(transport=transport, base_url="http://t")
    r = await c.post("/api/auth/login", json={"email": email, "password": "secret1"})
    assert r.status_code == 200, r.text
    return c, {"X-CSRF-Token": r.json()["csrf_token"]}


async def _id(client, email):
    return next(m["id"] for m in (await client.get("/api/members")).json()
               if m["email"] == email)


async def _age_out(maker, entry_id, minutes=31):
    async with maker() as s:
        e = await s.get(LedgerEntry, entry_id)
        e.created_at = utcnow() - timedelta(minutes=minutes)
        await s.commit()


async def test_member_edits_own_within_window(ctx):
    transport, _ = ctx
    c, h = await _login(transport)
    bob = await _id(c, "bob@nekopay.app")
    e = (await c.post("/api/topups", headers=h,
                      json={"member_id": bob, "money_nt": 1000})).json()
    r = await c.post(f"/api/ledger/{e['id']}/edit", headers=h, json={"points": 60})
    assert r.status_code == 200 and r.json()["points_delta"] == 60
    assert (await c.get(f"/api/members/{bob}/balance")).json()["points_balance"] == 60
    await c.aclose()


async def test_member_cannot_edit_after_30min(ctx):
    transport, maker = ctx
    c, h = await _login(transport)
    bob = await _id(c, "bob@nekopay.app")
    e = (await c.post("/api/topups", headers=h,
                      json={"member_id": bob, "money_nt": 1000})).json()
    await _age_out(maker, e["id"])
    r = await c.post(f"/api/ledger/{e['id']}/edit", headers=h, json={"points": 60})
    assert r.status_code == 403
    # delete also blocked after the window
    assert (await c.request("DELETE", f"/api/ledger/{e['id']}", headers=h)).status_code == 403
    await c.aclose()


async def test_member_cannot_edit_others_record(ctx):
    transport, _ = ctx
    admin, ah = await _login(transport, "admin@nekopay.app")
    adm = await _id(admin, "admin@nekopay.app")
    e = (await admin.post("/api/topups", headers=ah,
                          json={"member_id": adm, "money_nt": 100})).json()
    await admin.aclose()
    bob_c, bh = await _login(transport)
    r = await bob_c.post(f"/api/ledger/{e['id']}/edit", headers=bh, json={"points": 5})
    assert r.status_code == 403
    await bob_c.aclose()


async def test_admin_edits_any_record_anytime(ctx):
    transport, maker = ctx
    bob_c, bh = await _login(transport)
    bob = await _id(bob_c, "bob@nekopay.app")
    e = (await bob_c.post("/api/topups", headers=bh,
                          json={"member_id": bob, "money_nt": 1000})).json()
    await bob_c.aclose()
    await _age_out(maker, e["id"], minutes=999)  # long past the window
    admin, ah = await _login(transport, "admin@nekopay.app")
    r = await admin.post(f"/api/ledger/{e['id']}/edit", headers=ah, json={"points": 42})
    assert r.status_code == 200 and r.json()["points_delta"] == 42
    await admin.aclose()


async def test_delete_transfer_removes_both_sides(ctx):
    transport, maker = ctx
    from app.services import ledger_service
    c, h = await _login(transport)
    bob = await _id(c, "bob@nekopay.app")
    adm = await _id(c, "admin@nekopay.app")
    await c.post("/api/topups", headers=h,
                 json={"member_id": bob, "money_nt": 1000})
    tr = (await c.post("/api/transfers", headers=h,
                       json={"from_member_id": bob, "to_member_id": adm, "points": 30})).json()
    # delete one side -> both removed, balances restored
    assert (await c.request("DELETE", f"/api/ledger/{tr['out_entry']['id']}",
                            headers=h)).status_code == 200
    assert (await c.get(f"/api/members/{bob}/balance")).json()["points_balance"] == 100
    async with maker() as s:  # admin balance via DB (member can't read it via API)
        assert await ledger_service.get_balance(s, adm) == 0
    await c.aclose()


async def test_transfer_cannot_be_edited(ctx):
    transport, _ = ctx
    c, h = await _login(transport)
    bob = await _id(c, "bob@nekopay.app")
    adm = await _id(c, "admin@nekopay.app")
    await c.post("/api/topups", headers=h,
                 json={"member_id": bob, "money_nt": 1000})
    tr = (await c.post("/api/transfers", headers=h,
                       json={"from_member_id": bob, "to_member_id": adm, "points": 30})).json()
    r = await c.post(f"/api/ledger/{tr['out_entry']['id']}/edit", headers=h, json={"points": 5})
    assert r.status_code == 400
    await c.aclose()


async def test_delete_attributed_entry_frees_real_txn(ctx):
    transport, maker = ctx
    async with maker() as s:
        s.add(RealTransaction(
            kind="pay", shop="竹喵店", machine="Chunithm", raw_name="竹喵店 - Chunithm",
            value=-3, pay_type="point", occurred_at=utcnow(),
            occurred_date_raw="06/10", occurred_time_raw="20:47",
            base_hash="z1", dedup_key="z1", occurrence_index=0))
        await s.commit()
    c, h = await _login(transport)
    cand = (await c.post("/api/attribution/match", headers=h,
                         json={"kind": "pay", "points": 3})).json()["candidates"][0]
    entry = (await c.post(f"/api/attribution/self/{cand['id']}", headers=h, json={})).json()
    # delete the attributed entry -> the real txn returns to unattributed
    assert (await c.request("DELETE", f"/api/ledger/{entry['id']}",
                            headers=h)).status_code == 200
    async with maker() as s:
        rt = await s.get(RealTransaction, cand["id"])
        assert rt.attribution_status == "unattributed"
        assert rt.attributed_member_id is None and rt.ledger_entry_id is None
    await c.aclose()


async def test_force_delete_member_purges_records(ctx):
    transport, maker = ctx
    admin, ah = await _login(transport, "admin@nekopay.app")
    bob = await _id(admin, "bob@nekopay.app")
    await admin.post("/api/topups", headers=ah, json={"member_id": bob, "money_nt": 500})
    # normal delete blocked (has a record)
    assert (await admin.request("DELETE", f"/api/members/{bob}",
                                headers=ah)).status_code == 409
    # force delete succeeds and removes the records
    assert (await admin.request("DELETE", f"/api/members/{bob}?force=true",
                                headers=ah)).status_code == 200
    async with maker() as s:
        cnt = (await s.execute(
            select(func.count()).select_from(LedgerEntry)
            .where(LedgerEntry.member_id == bob))).scalar_one()
        assert cnt == 0
    assert bob not in [m["id"] for m in (await admin.get("/api/members")).json()]
    await admin.aclose()


async def test_force_delete_frees_attributed_real_txn(ctx):
    transport, maker = ctx
    admin, ah = await _login(transport, "admin@nekopay.app")
    bob = await _id(admin, "bob@nekopay.app")
    async with maker() as s:
        s.add(RealTransaction(
            kind="pay", shop="竹喵店", machine="Chunithm", raw_name="竹喵店 - Chunithm",
            value=-3, pay_type="point", occurred_at=utcnow(),
            occurred_date_raw="06/10", occurred_time_raw="20:47",
            base_hash="fd1", dedup_key="fd1", occurrence_index=0))
        await s.commit()
        rid = (await s.execute(
            select(RealTransaction.id).where(RealTransaction.dedup_key == "fd1"))
        ).scalar_one()
    # admin attributes it to bob, then force-deletes bob
    assert (await admin.post(f"/api/admin/real-transactions/{rid}/attribute",
            headers=ah, json={"member_id": bob})).status_code == 200
    assert (await admin.request("DELETE", f"/api/members/{bob}?force=true",
            headers=ah)).status_code == 200
    async with maker() as s:
        rt = await s.get(RealTransaction, rid)
        assert rt.attribution_status == "unattributed" and rt.attributed_member_id is None
    await admin.aclose()


async def test_edit_attributed_amount_blocked_note_ok(ctx):
    transport, maker = ctx
    async with maker() as s:
        s.add(RealTransaction(
            kind="pay", shop="竹喵店", machine="Chunithm", raw_name="竹喵店 - Chunithm",
            value=-3, pay_type="point", occurred_at=utcnow(),
            occurred_date_raw="06/10", occurred_time_raw="20:47",
            base_hash="z2", dedup_key="z2", occurrence_index=0))
        await s.commit()
    c, h = await _login(transport)
    cand = (await c.post("/api/attribution/match", headers=h,
                         json={"kind": "pay", "points": 3})).json()["candidates"][0]
    entry = (await c.post(f"/api/attribution/self/{cand['id']}", headers=h, json={})).json()
    # amount edit blocked
    assert (await c.post(f"/api/ledger/{entry['id']}/edit", headers=h,
                         json={"points": 9})).status_code == 400
    # note edit allowed
    r = await c.post(f"/api/ledger/{entry['id']}/edit", headers=h, json={"note": "fixed"})
    assert r.status_code == 200 and r.json()["note"] == "fixed"
    await c.aclose()
