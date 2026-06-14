"""Attribution + reconciliation tests."""
from __future__ import annotations

from decimal import Decimal

import pytest

from app.models.real import AccountSnapshot, RealTransaction
from app.services import ledger_service as svc
from app.services.attribution_service import attribute
from app.services.errors import ConflictError
from app.services.reconciliation import reconcile_report
from app.util.time import utcnow

RATE = Decimal("1.0")


async def _make_real(session, *, kind, value, name, key):
    rt = RealTransaction(
        kind=kind,
        shop=name.split(" - ")[0],
        machine=(name.split(" - ")[1] if " - " in name else None),
        raw_name=name,
        value=value,
        pay_type=("point" if kind == "pay" else None),
        occurred_at=utcnow(),
        occurred_date_raw="06/10",
        occurred_time_raw="18:07",
        base_hash=key,
        dedup_key=key,
        occurrence_index=0,
    )
    session.add(rt)
    await session.commit()
    await session.refresh(rt)
    return rt


async def test_attribute_topup_and_play(session, create_member):
    admin = await create_member("admin", role="admin")
    alice = await create_member("alice")
    top = await _make_real(session, kind="topup", value=33, name="竹喵店", key="h1")
    pay = await _make_real(
        session, kind="pay", value=-3, name="竹喵店 - Chunithm", key="h2"
    )

    await attribute(
        session, real_txn_id=top.id, member_id=alice.id, actor_id=admin.id, rate=RATE
    )
    await attribute(
        session, real_txn_id=pay.id, member_id=alice.id, actor_id=admin.id, rate=RATE
    )
    assert await svc.get_balance(session, alice.id) == 30
    assert await svc.get_money_contributed(session, alice.id) == Decimal("33")

    # double-attribution rejected
    with pytest.raises(ConflictError):
        await attribute(
            session, real_txn_id=top.id, member_id=alice.id, actor_id=admin.id, rate=RATE
        )


async def test_reconciliation_drift(session, create_member):
    admin = await create_member("admin", role="admin")
    alice = await create_member("alice")
    top = await _make_real(session, kind="topup", value=30, name="竹喵店", key="h1")
    await attribute(
        session, real_txn_id=top.id, member_id=alice.id, actor_id=admin.id, rate=RATE
    )

    rep = await reconcile_report(session)
    assert rep.internal_total == 30
    assert rep.pooled_balance is None  # no snapshot yet
    assert rep.unattributed_count == 0
    assert rep.manual_entry_count == 0  # entry was attribution-sourced

    session.add(AccountSnapshot(balance=30))
    await session.commit()
    rep = await reconcile_report(session)
    assert rep.pooled_balance == 30
    assert rep.drift == 0
