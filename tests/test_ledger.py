"""Tests for the append-only ledger service: balances, atomic transfer,
idempotency, no-overdraft, reversal."""
from __future__ import annotations

from decimal import Decimal

import pytest

from app.services import ledger_service as svc
from app.services.errors import (
    InsufficientBalanceError,
    NotFoundError,
    ValidationError,
)


async def test_topup_then_play_balance(session, create_member):
    a = await create_member("alice")
    await svc.record_topup(
        session, member_id=a.id, points=100, money_nt=100, created_by=a.id
    )
    assert await svc.get_balance(session, a.id) == 100
    assert await svc.get_money_contributed(session, a.id) == Decimal("100")

    await svc.record_play(session, member_id=a.id, points=30, created_by=a.id)
    assert await svc.get_balance(session, a.id) == 70


async def test_play_overdraft_blocked(session, create_member):
    a = await create_member("alice")
    await svc.record_topup(
        session, member_id=a.id, points=20, money_nt=20, created_by=a.id
    )
    with pytest.raises(InsufficientBalanceError):
        await svc.record_play(session, member_id=a.id, points=50, created_by=a.id)
    # admin override creates a debt
    await svc.record_play(
        session, member_id=a.id, points=50, created_by=a.id, allow_negative=True
    )
    assert await svc.get_balance(session, a.id) == -30


async def test_topup_validation(session, create_member):
    a = await create_member("alice")
    with pytest.raises(ValidationError):
        await svc.record_topup(
            session, member_id=a.id, points=0, money_nt=10, created_by=a.id
        )
    with pytest.raises(ValidationError):
        await svc.record_topup(
            session, member_id=a.id, points=10, money_nt=0, created_by=a.id
        )


async def test_transfer_is_atomic_and_conserves_total(session, create_member):
    a = await create_member("alice")
    b = await create_member("bob")
    await svc.record_topup(
        session, member_id=a.id, points=100, money_nt=100, created_by=a.id
    )
    total_before = await svc.get_internal_total(session)

    out_row, in_row = await svc.transfer(
        session, from_member_id=a.id, to_member_id=b.id, points=40, created_by=a.id
    )
    assert out_row.transfer_group_id == in_row.transfer_group_id
    assert await svc.get_balance(session, a.id) == 60
    assert await svc.get_balance(session, b.id) == 40
    # transfer must not change the reconciliation total
    assert await svc.get_internal_total(session) == total_before


async def test_transfer_to_self_rejected(session, create_member):
    a = await create_member("alice")
    await svc.record_topup(
        session, member_id=a.id, points=50, money_nt=50, created_by=a.id
    )
    with pytest.raises(ValidationError):
        await svc.transfer(
            session, from_member_id=a.id, to_member_id=a.id, points=10, created_by=a.id
        )


async def test_transfer_overdraft_blocked(session, create_member):
    a = await create_member("alice")
    b = await create_member("bob")
    with pytest.raises(InsufficientBalanceError):
        await svc.transfer(
            session, from_member_id=a.id, to_member_id=b.id, points=10, created_by=a.id
        )


async def test_topup_idempotency(session, create_member):
    a = await create_member("alice")
    e1 = await svc.record_topup(
        session,
        member_id=a.id,
        points=100,
        money_nt=100,
        created_by=a.id,
        idempotency_key="k-1",
    )
    e2 = await svc.record_topup(
        session,
        member_id=a.id,
        points=100,
        money_nt=100,
        created_by=a.id,
        idempotency_key="k-1",
    )
    assert e1.id == e2.id
    assert await svc.get_balance(session, a.id) == 100  # not doubled


async def test_transfer_idempotency(session, create_member):
    a = await create_member("alice")
    b = await create_member("bob")
    await svc.record_topup(
        session, member_id=a.id, points=100, money_nt=100, created_by=a.id
    )
    o1, i1 = await svc.transfer(
        session,
        from_member_id=a.id,
        to_member_id=b.id,
        points=30,
        created_by=a.id,
        idempotency_key="t-1",
    )
    o2, i2 = await svc.transfer(
        session,
        from_member_id=a.id,
        to_member_id=b.id,
        points=30,
        created_by=a.id,
        idempotency_key="t-1",
    )
    assert o1.id == o2.id and i1.id == i2.id
    assert await svc.get_balance(session, a.id) == 70
    assert await svc.get_balance(session, b.id) == 30


async def test_reversal_negates_points_and_money(session, create_member):
    a = await create_member("alice")
    e = await svc.record_topup(
        session, member_id=a.id, points=100, money_nt=100, created_by=a.id
    )
    assert await svc.get_balance(session, a.id) == 100
    await svc.reverse_entry(session, entry_id=e.id, created_by=a.id, reason="mistake")
    assert await svc.get_balance(session, a.id) == 0
    assert await svc.get_money_contributed(session, a.id) == Decimal("0")


async def test_unknown_member_rejected(session):
    with pytest.raises(NotFoundError):
        await svc.record_topup(
            session, member_id=999, points=10, money_nt=10, created_by=999
        )
