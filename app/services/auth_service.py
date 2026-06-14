"""Password hashing + server-side session management."""
from __future__ import annotations

import secrets
from datetime import timedelta

from passlib.context import CryptContext
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.auth import UserSession
from app.models.user import Member
from app.services.errors import ValidationError
from app.util.time import utcnow

_pwd = CryptContext(schemes=["argon2"], deprecated="auto")


def hash_password(password: str) -> str:
    if not password or len(password) < 6:
        raise ValidationError("password must be at least 6 characters")
    return _pwd.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return _pwd.verify(password, password_hash)
    except ValueError:
        return False


async def authenticate(
    session: AsyncSession, email: str, password: str
) -> Member | None:
    member = (
        await session.execute(select(Member).where(Member.email == email.lower()))
    ).scalar_one_or_none()
    if member is None or not member.is_active:
        return None
    if not verify_password(password, member.password_hash):
        return None
    return member


async def create_session(
    session: AsyncSession, member_id: int, ttl_hours: int
) -> UserSession:
    us = UserSession(
        token=secrets.token_urlsafe(32),
        member_id=member_id,
        csrf_token=secrets.token_urlsafe(24),
        expires_at=utcnow() + timedelta(hours=ttl_hours),
    )
    session.add(us)
    await session.commit()
    await session.refresh(us)
    return us


async def get_session(session: AsyncSession, token: str) -> UserSession | None:
    if not token:
        return None
    us = await session.get(UserSession, token)
    if us is None:
        return None
    if us.expires_at < utcnow():
        await session.delete(us)
        await session.commit()
        return None
    return us


async def delete_session(session: AsyncSession, token: str) -> None:
    await session.execute(delete(UserSession).where(UserSession.token == token))
    await session.commit()
