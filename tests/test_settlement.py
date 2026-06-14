"""Settlement position + minimal-transaction tests."""
from __future__ import annotations

from decimal import Decimal

from app.services import ledger_service as svc
from app.services.settlement import compute_positions, settle

RATE = Decimal("1.0")


async def test_positions_reflect_contribution_and_consumption(session, create_member):
    a = await create_member("alice")
    await svc.record_topup(
        session, member_id=a.id, points=300, money_nt=300, created_by=a.id
    )
    await svc.record_play(session, member_id=a.id, points=100, created_by=a.id)

    positions = await compute_positions(session, RATE)
    pa = next(p for p in positions if p.member_id == a.id)
    assert pa.contributed_nt == Decimal("300")
    assert pa.consumed_points == 100
    assert pa.balance_points == 200
    assert pa.fairness_net_nt == Decimal("200.00")


async def test_settlement_simple_pairwise(session, create_member):
    a = await create_member("alice")
    b = await create_member("bob")
    # alice paid 100, didn't play -> creditor; bob played 100 without paying -> debtor
    await svc.record_topup(
        session, member_id=a.id, points=100, money_nt=100, created_by=a.id
    )
    await svc.record_play(
        session, member_id=b.id, points=100, created_by=b.id, allow_negative=True
    )

    positions = await compute_positions(session, RATE)
    txns = settle(positions)
    assert len(txns) == 1
    t = txns[0]
    assert t.from_member_id == b.id and t.to_member_id == a.id
    assert t.amount_nt == Decimal("100.00")


async def test_settlement_leaves_unspent_pool_with_creditor(session, create_member):
    a = await create_member("alice")
    c = await create_member("carol")
    await svc.record_topup(
        session, member_id=a.id, points=300, money_nt=300, created_by=a.id
    )
    await svc.record_play(session, member_id=a.id, points=100, created_by=a.id)
    await svc.record_play(
        session, member_id=c.id, points=100, created_by=c.id, allow_negative=True
    )
    # net: alice +200, carol -100 ; one transfer carol->alice 100
    txns = settle(await compute_positions(session, RATE))
    assert len(txns) == 1
    assert txns[0].from_member_id == c.id and txns[0].amount_nt == Decimal("100.00")


async def test_settlement_balanced_group_no_txns(session, create_member):
    a = await create_member("alice")
    await svc.record_topup(
        session, member_id=a.id, points=50, money_nt=50, created_by=a.id
    )
    await svc.record_play(session, member_id=a.id, points=50, created_by=a.id)
    # contributed 50, consumed 50 -> net 0 -> nothing to settle
    assert settle(await compute_positions(session, RATE)) == []
