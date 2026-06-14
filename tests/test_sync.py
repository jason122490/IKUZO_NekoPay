"""Sync cycle tests against a mocked NekoPay API (respx) + real SQLite."""
from __future__ import annotations

import respx
from httpx import Response
from sqlalchemy import func, select

from app.models.real import AccountSnapshot, RealTransaction
from app.services.nekopay_client import NekoPayClient
from app.services.sync_service import run_sync_cycle
from app.services.token_manager import TokenManager

BASE = "https://shironekoya.net"
TZ = "Asia/Taipei"

HISTORY = {
    "topup": [{"time": {"date": "06/10", "time": "18:07"}, "name": "竹喵店", "value": 33}],
    "pay": [
        {
            "time": {"date": "06/10", "time": "20:47"},
            "name": "竹喵店 - Chunithm",
            "value": 3,
            "type": "point",
        }
    ],
}


def _mock_ok(history):
    respx.get(f"{BASE}/index/login/do_login").mock(
        return_value=Response(200, json={"code": "1", "msg": "ok", "data": {"token": "T"}})
    )
    respx.get(f"{BASE}/index/nekopay/user_info").mock(
        return_value=Response(
            200,
            json={"code": "0", "data": {"balance": 100, "cardId": 1, "status": "normal"}},
        )
    )
    respx.get(f"{BASE}/index/Nekopay/getPayHistory").mock(
        return_value=Response(200, json={"code": "0", "msg": "Success", "data": history})
    )


async def _count(session, model) -> int:
    return (await session.execute(select(func.count()).select_from(model))).scalar_one()


async def test_sync_inserts_then_idempotent(session):
    with respx.mock:
        _mock_ok(HISTORY)
        client = NekoPayClient(BASE, "UA")
        tm = TokenManager(client, "e", "p")

        run1 = await run_sync_cycle(
            session, client, tm, TZ, transport_retries=1, backoff_base=0
        )
        assert run1.status == "ok"
        assert run1.rows_inserted == 2

        run2 = await run_sync_cycle(
            session, client, tm, TZ, transport_retries=1, backoff_base=0
        )
        assert run2.rows_inserted == 0  # idempotent replay
        await client.aclose()

    assert await _count(session, RealTransaction) == 2
    assert await _count(session, AccountSnapshot) == 2  # one per cycle


async def test_sync_refreshes_token_on_auth_error(session):
    with respx.mock:
        login = respx.get(f"{BASE}/index/login/do_login").mock(
            return_value=Response(200, json={"code": "1", "data": {"token": "T"}})
        )
        respx.get(f"{BASE}/index/nekopay/user_info").mock(
            side_effect=[
                Response(200, json={"code": "9", "msg": "bad token"}),
                Response(200, json={"code": "0", "data": {"balance": 0}}),
            ]
        )
        respx.get(f"{BASE}/index/Nekopay/getPayHistory").mock(
            return_value=Response(200, json={"code": "0", "data": {"topup": [], "pay": []}})
        )
        client = NekoPayClient(BASE, "UA")
        tm = TokenManager(client, "e", "p")
        run = await run_sync_cycle(
            session, client, tm, TZ, transport_retries=1, backoff_base=0
        )
        assert run.status == "ok"
        assert login.call_count == 2  # initial login + reactive refresh
        await client.aclose()


async def test_sync_respects_since_cutoff(session):
    from app.services import config_service
    await config_service.set_sync_since(session, "2026-06-10")
    history = {
        "topup": [{"time": {"date": "06/12", "time": "18:07"}, "name": "竹喵店", "value": 33}],
        "pay": [{"time": {"date": "06/05", "time": "10:00"},
                 "name": "竹喵店 - Chunithm", "value": 3, "type": "point"}],
    }
    with respx.mock:
        _mock_ok(history)
        client = NekoPayClient(BASE, "UA")
        tm = TokenManager(client, "e", "p")
        run = await run_sync_cycle(
            session, client, tm, TZ, transport_retries=1, backoff_base=0
        )
        assert run.status == "ok"
        assert run.rows_inserted == 1  # 06/12 kept; 06/05 is before the cutoff
        await client.aclose()


async def test_sync_records_auth_failure(session):
    with respx.mock:
        respx.get(f"{BASE}/index/login/do_login").mock(
            return_value=Response(200, json={"code": "0", "msg": "wrong password"})
        )
        client = NekoPayClient(BASE, "UA")
        tm = TokenManager(client, "e", "bad")
        run = await run_sync_cycle(
            session, client, tm, TZ, transport_retries=1, backoff_base=0
        )
        assert run.status == "auth_failed"
        await client.aclose()
    assert await _count(session, RealTransaction) == 0
