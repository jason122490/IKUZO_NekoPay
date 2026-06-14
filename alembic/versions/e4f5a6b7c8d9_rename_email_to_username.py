"""rename members.email to username

Revision ID: e4f5a6b7c8d9
Revises: d3e4f5a6b7c8
Create Date: 2026-06-15
"""
from typing import Sequence, Union

from alembic import op

revision: str = "e4f5a6b7c8d9"
down_revision: Union[str, None] = "d3e4f5a6b7c8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TABLE members RENAME COLUMN email TO username")
    op.execute("DROP INDEX IF EXISTS ix_members_email")
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS ix_members_username ON members (username)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_members_username")
    op.execute("ALTER TABLE members RENAME COLUMN username TO email")
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS ix_members_email ON members (email)"
    )
