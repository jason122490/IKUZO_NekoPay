"""DB reset is FK-safe (production has foreign_keys=ON, unlike other tests)."""
from __future__ import annotations

import pytest_asyncio
from sqlalchemy import event, func, select
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.db import Base
from app.models.auth import UserSession
from app.models.ledger import LedgerEntry
from app.models.real import AccountSnapshot, RealTransaction
from app.models.user import Member
from app.services import member_admin
from app.util.time import utcnow


@pytest_asyncio.fixture
async def fk_session():
    engine = create_async_engine(
        "sqlite+aiosqlite://", connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine.sync_engine, "connect")
    def _fk(dbapi_conn, _rec):  # match production FK enforcement
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

    async with engine.begin() as c:
        await c.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with maker() as s:
        yield s, maker
    await engine.dispose()


async def test_reset_database_is_fk_safe(fk_session):
    s, maker = fk_session
    admin = Member(username="a@x.com", display_name="A", password_hash="x", role="admin")
    bob = Member(username="b@x.com", display_name="B", password_hash="x", role="member")
    s.add_all([admin, bob])
    await s.commit()

    rt = RealTransaction(
        kind="pay", shop="x", raw_name="x", value=-3, occurred_at=utcnow(),
        occurred_date_raw="06/10", occurred_time_raw="10:00",
        base_hash="h", dedup_key="h")
    s.add(rt)
    await s.commit()
    entry = LedgerEntry(member_id=bob.id, entry_type="PLAY", points_delta=-3,
                        created_by=admin.id, source_real_txn_id=rt.id)
    s.add(entry)
    await s.commit()
    # a reversal pointing at the entry -> exercises the self-referential FK
    s.add(LedgerEntry(member_id=bob.id, entry_type="ADJUSTMENT", points_delta=3,
                      created_by=admin.id, reversal_of_id=entry.id))
    s.add(UserSession(token="t", member_id=bob.id, csrf_token="c",
                      expires_at=utcnow()))
    s.add(AccountSnapshot(balance=0))
    await s.commit()

    # must not raise under FK enforcement
    await member_admin.reset_database(s, keep_member_id=admin.id)

    async with maker() as s2:
        assert [m.id for m in (await s2.execute(select(Member))).scalars()] == [admin.id]
        for model in (LedgerEntry, RealTransaction, AccountSnapshot, UserSession):
            cnt = (await s2.execute(select(func.count()).select_from(model))).scalar_one()
            assert cnt == 0, model.__name__
