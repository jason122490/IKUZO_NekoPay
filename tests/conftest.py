"""Shared test fixtures: an isolated in-memory SQLite session per test."""
from __future__ import annotations

import pytest_asyncio
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401  -- registers tables on Base.metadata
from app.db import Base
from app.models.user import Member


@pytest_asyncio.fixture
async def session():
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with maker() as s:
        yield s
    await engine.dispose()


@pytest_asyncio.fixture
async def create_member(session):
    async def _make(name: str, *, role: str = "member") -> Member:
        m = Member(
            username=f"{name}@test.local",
            display_name=name,
            password_hash="x",
            role=role,
        )
        session.add(m)
        await session.commit()
        await session.refresh(m)
        return m

    return _make
