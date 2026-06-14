"""add vip fields to account_snapshots

Revision ID: c2d3e4f5a6b7
Revises: b1a2c3d4e5f6
Create Date: 2026-06-14
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "c2d3e4f5a6b7"
down_revision: Union[str, None] = "b1a2c3d4e5f6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("account_snapshots") as batch:
        batch.add_column(sa.Column("vip_name", sa.String(length=32), nullable=True))
        batch.add_column(sa.Column("vip_next_value", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("is_premium", sa.Boolean(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("account_snapshots") as batch:
        batch.drop_column("is_premium")
        batch.drop_column("vip_next_value")
        batch.drop_column("vip_name")
