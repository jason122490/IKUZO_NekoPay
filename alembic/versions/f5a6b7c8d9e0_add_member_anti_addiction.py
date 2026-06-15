"""add member anti-addiction (toggle + daily spend limit)

Revision ID: f5a6b7c8d9e0
Revises: e4f5a6b7c8d9
Create Date: 2026-06-15
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "f5a6b7c8d9e0"
down_revision: Union[str, None] = "e4f5a6b7c8d9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("members") as batch:
        batch.add_column(
            sa.Column(
                "anti_addiction",
                sa.Boolean(),
                nullable=False,
                server_default=sa.text("1"),
            )
        )
        batch.add_column(
            sa.Column(
                "daily_spend_limit",
                sa.Integer(),
                nullable=False,
                server_default=sa.text("30"),
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("members") as batch:
        batch.drop_column("daily_spend_limit")
        batch.drop_column("anti_addiction")
