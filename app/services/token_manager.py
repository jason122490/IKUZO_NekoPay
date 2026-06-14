"""In-memory NekoPay token holder with reactive refresh.

The token has no advertised TTL, so expiry is detected reactively: an authed
call raising NekoPayAuthError triggers exactly one re-login + retry. A lock
coalesces concurrent logins.
"""
from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import TypeVar

from app.services.nekopay_client import NekoPayAuthError, NekoPayClient

T = TypeVar("T")


class TokenManager:
    def __init__(self, client: NekoPayClient, email: str, password: str):
        self._client = client
        self._email = email
        self._password = password
        self._token: str | None = None
        self._lock = asyncio.Lock()

    @property
    def cached(self) -> str | None:
        return self._token

    def prime(self, token: str | None) -> None:
        """Seed a warm-start token (e.g. decrypted from SyncState)."""
        self._token = token

    async def get_token(self, force: bool = False) -> str:
        async with self._lock:
            if force or not self._token:
                self._token = await self._client.login(self._email, self._password)
            return self._token

    async def call_with_retry(
        self, factory: Callable[[str], Awaitable[T]]
    ) -> T:
        """Run an authed call; on auth failure, refresh the token once and retry."""
        token = await self.get_token()
        try:
            return await factory(token)
        except NekoPayAuthError:
            token = await self.get_token(force=True)
            return await factory(token)
