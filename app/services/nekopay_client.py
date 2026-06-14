"""Async HTTP client for the real NekoPay (shironekoya.net) API.

Notes baked in from the reverse-engineered docs:
  * a browser User-Agent is mandatory (Cloudflare returns 403 otherwise);
  * the response envelope is always HTTP 200 with a STRING ``code`` whose
    success value differs per endpoint (do_login -> "1", nekopay/* -> "0");
  * one AsyncClient is reused so the Cloudflare cookie jar persists.
"""
from __future__ import annotations

from typing import Any

import httpx


class NekoPayError(Exception):
    """Base error for the NekoPay client."""


class NekoPayAuthError(NekoPayError):
    """Login failed or the token is invalid/expired."""


class NekoPayTransportError(NekoPayError):
    """Network/timeout/HTTP error (incl. Cloudflare 403)."""


class NekoPayProtocolError(NekoPayError):
    """Unexpected response shape."""


class NekoPayClient:
    def __init__(
        self,
        base_url: str,
        user_agent: str,
        *,
        timeout: float = 10.0,
        client: httpx.AsyncClient | None = None,
    ):
        self.base_url = base_url.rstrip("/")
        self._headers = {
            "User-Agent": user_agent,
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "zh-TW,zh;q=0.9",
            "Referer": f"{self.base_url}/",
        }
        self._timeout = httpx.Timeout(timeout, connect=5.0)
        self._client = client  # injectable for tests

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                headers=self._headers,
                timeout=self._timeout,
                follow_redirects=True,
            )
        return self._client

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def _request(self, path: str, params: dict[str, Any]) -> dict:
        client = await self._get_client()
        try:
            resp = await client.get(path, params=params)
            resp.raise_for_status()
        except httpx.HTTPError as exc:  # timeouts, 4xx/5xx, connect errors
            raise NekoPayTransportError(f"{path}: {exc!r}") from exc
        try:
            body = resp.json()
        except ValueError as exc:
            raise NekoPayProtocolError(f"{path}: non-JSON response") from exc
        if not isinstance(body, dict) or "code" not in body:
            raise NekoPayProtocolError(f"{path}: unexpected envelope {body!r:.120}")
        return body

    async def login(self, email: str, password: str) -> str:
        body = await self._request(
            "/index/login/do_login", {"email": email, "password": password}
        )
        if str(body.get("code")) != "1":
            raise NekoPayAuthError(f"login failed: {body.get('msg')!r}")
        token = (body.get("data") or {}).get("token")
        if not token:
            raise NekoPayProtocolError("login ok but no token in response")
        return token

    async def get_user_info(self, token: str) -> dict:
        body = await self._request("/index/nekopay/user_info", {"token": token})
        if str(body.get("code")) != "0":
            raise NekoPayAuthError(f"user_info code={body.get('code')!r}")
        return body.get("data") or {}

    async def get_pay_history(self, token: str) -> dict:
        body = await self._request("/index/Nekopay/getPayHistory", {"token": token})
        if str(body.get("code")) != "0":
            raise NekoPayAuthError(f"getPayHistory code={body.get('code')!r}")
        return body.get("data") or {}
