"""add vip_cumulative to account_snapshots

Revision ID: d3e4f5a6b7c8
Revises: c2d3e4f5a6b7
Create Date: 2026-06-15
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "d3e4f5a6b7c8"
down_revision: Union[str, None] = "c2d3e4f5a6b7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("account_snapshots") as batch:
        batch.add_column(sa.Column("vip_cumulative", sa.Integer(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("account_snapshots") as batch:
        batch.drop_column("vip_cumulative")
