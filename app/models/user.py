"""Member (internal user) model."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, String, text
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models.enums import Role
from app.util.time import utcnow


class Member(Base):
    __tablename__ = "members"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    display_name: Mapped[str] = mapped_column(String(100))
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(16), default=Role.MEMBER.value)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    # When True (default), 投幣/儲值 first try to match an unattributed real txn.
    auto_attribute: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default=text("1")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    @property
    def is_admin(self) -> bool:
        return self.role == Role.ADMIN.value
