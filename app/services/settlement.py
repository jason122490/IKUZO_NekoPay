"""Settlement ("who owes whom") + position math.

Two views are produced:
  * **balance equity**  - each member's current points balance x rate = their
    stake in the shared pool (what they'd get back / owe if cashed out now).
  * **fairness net**    - contributed_money - consumed_points x rate. This is
    the basis for the pairwise "who owes whom" minimal-transaction settlement,
    since it nets to zero across members (residual = unspent pool value).
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.enums import EntryType
from app.models.ledger import LedgerEntry
from app.models.user import Member

CENT = Decimal("0.01")


@dataclass
class Position:
    member_id: int
    display_name: str
    contributed_nt: Decimal
    consumed_points: int
    balance_points: int
    rate: Decimal  # effective NT$/point for this member (money paid / points got)

    @property
    def consumed_value_nt(self) -> Decimal:
        return (Decimal(self.consumed_points) * self.rate).quantize(CENT)

    @property
    def balance_value_nt(self) -> Decimal:
        return (Decimal(self.balance_points) * self.rate).quantize(CENT)

    @property
    def fairness_net_nt(self) -> Decimal:
        return (self.contributed_nt - self.consumed_value_nt).quantize(CENT)


@dataclass
class SettlementTxn:
    from_member_id: int
    from_name: str
    to_member_id: int
    to_name: str
    amount_nt: Decimal


async def compute_positions(
    session: AsyncSession, rate: Decimal
) -> list[Position]:
    members = list(
        (await session.execute(select(Member).where(Member.is_active.is_(True))))
        .scalars()
    )

    balance_rows = dict(
        (
            await session.execute(
                select(
                    LedgerEntry.member_id,
                    func.coalesce(func.sum(LedgerEntry.points_delta), 0),
                ).group_by(LedgerEntry.member_id)
            )
        ).all()
    )
    money_rows = dict(
        (
            await session.execute(
                select(
                    LedgerEntry.member_id,
                    func.coalesce(func.sum(LedgerEntry.money_nt), 0),
                )
                .where(LedgerEntry.money_nt.is_not(None))
                .group_by(LedgerEntry.member_id)
            )
        ).all()
    )
    consumed_rows = dict(
        (
            await session.execute(
                select(
                    LedgerEntry.member_id,
                    func.coalesce(func.sum(LedgerEntry.points_delta), 0),
                )
                .where(LedgerEntry.entry_type == EntryType.PLAY.value)
                .group_by(LedgerEntry.member_id)
            )
        ).all()
    )

    # points each member received from their own paid top-ups (money_nt set);
    # incl. VIP bonus, since the bonus is part of the same top-up row's points.
    topup_points_rows = dict(
        (
            await session.execute(
                select(
                    LedgerEntry.member_id,
                    func.coalesce(func.sum(LedgerEntry.points_delta), 0),
                )
                .where(LedgerEntry.money_nt.is_not(None))
                .group_by(LedgerEntry.member_id)
            )
        ).all()
    )

    positions = []
    for m in members:
        contributed = Decimal(str(money_rows.get(m.id, 0)))
        received = int(topup_points_rows.get(m.id, 0))
        # value this member's points at what they actually paid per point
        # (money / points received). Free VIP bonus points thus lower the
        # per-point cost instead of pushing the fairness net negative. Falls
        # back to the global rate when there are no paid top-ups.
        eff_rate = (contributed / Decimal(received)) if received > 0 else rate
        positions.append(
            Position(
                member_id=m.id,
                display_name=m.display_name,
                contributed_nt=contributed,
                consumed_points=-int(consumed_rows.get(m.id, 0)),  # PLAY deltas are neg
                balance_points=int(balance_rows.get(m.id, 0)),
                rate=eff_rate,
            )
        )
    return positions


def settle(positions: list[Position]) -> list[SettlementTxn]:
    """Greedy minimal-transaction settlement on the fairness net (<= n-1 txns)."""
    creditors = sorted(
        ([p.fairness_net_nt, p] for p in positions if p.fairness_net_nt > CENT),
        key=lambda x: x[0],
        reverse=True,
    )
    debtors = sorted(
        ([-p.fairness_net_nt, p] for p in positions if p.fairness_net_nt < -CENT),
        key=lambda x: x[0],
        reverse=True,
    )

    txns: list[SettlementTxn] = []
    ci = di = 0
    while ci < len(creditors) and di < len(debtors):
        cred = creditors[ci]
        debt = debtors[di]
        amount = min(cred[0], debt[0])
        txns.append(
            SettlementTxn(
                from_member_id=debt[1].member_id,
                from_name=debt[1].display_name,
                to_member_id=cred[1].member_id,
                to_name=cred[1].display_name,
                amount_nt=amount.quantize(CENT),
            )
        )
        cred[0] -= amount
        debt[0] -= amount
        if cred[0] <= CENT:
            ci += 1
        if debt[0] <= CENT:
            di += 1
    return txns
