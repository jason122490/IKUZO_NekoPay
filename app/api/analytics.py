"""Analytics + settlement endpoints."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db import get_session
from app.models.user import Member
from app.schemas import PositionOut, SettlementOut, SettlementTxnOut
from app.security import get_current_member
from app.services import config_service
from app.services.settlement import compute_positions, settle

router = APIRouter(prefix="/api/analytics", tags=["analytics"])
settings = get_settings()


@router.get("/settlement", response_model=SettlementOut)
async def settlement(
    _viewer: Member = Depends(get_current_member),
    session: AsyncSession = Depends(get_session),
) -> SettlementOut:
    rate = await config_service.get_rate(session, settings.default_rate_nt_per_point)
    positions = await compute_positions(session, rate)
    txns = settle(positions)
    return SettlementOut(
        rate_nt_per_point=rate,
        positions=[
            PositionOut(
                member_id=p.member_id,
                display_name=p.display_name,
                contributed_nt=p.contributed_nt,
                consumed_points=p.consumed_points,
                consumed_value_nt=p.consumed_value_nt,
                balance_points=p.balance_points,
                balance_value_nt=p.balance_value_nt,
                fairness_net_nt=p.fairness_net_nt,
            )
            for p in positions
        ],
        transactions=[
            SettlementTxnOut(
                from_member_id=t.from_member_id,
                from_name=t.from_name,
                to_member_id=t.to_member_id,
                to_name=t.to_name,
                amount_nt=t.amount_nt,
            )
            for t in txns
        ],
    )
