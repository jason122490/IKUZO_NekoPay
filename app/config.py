"""Application configuration via pydantic-settings.

Secrets come from environment / .env (never committed). In production prefer
Docker secrets / host env, which override .env automatically.
"""
from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    env: str = "dev"

    # Database
    database_url: str = "sqlite+aiosqlite:///./data/nekopay.db"

    # App secrets
    secret_key: str = "dev-insecure-session-key-change-me"
    secret_encryption_key: str = ""  # Fernet key; required to persist cached token

    # Shared NekoPay account (server-side sync only)
    nekopay_base_url: str = "https://shironekoya.net"
    nekopay_email: str = ""
    nekopay_password: str = ""
    nekopay_user_agent: str = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
    )

    # Sync
    run_scheduler: bool = True
    sync_interval_seconds: int = 300
    app_timezone: str = "Asia/Taipei"

    # Economics
    default_rate_nt_per_point: float = 1.0

    # Bootstrap admin
    admin_bootstrap_email: str = ""
    admin_bootstrap_password: str = ""

    # Sessions / cookies
    session_cookie_name: str = "nekopay_session"
    session_ttl_hours: int = 168  # 7 days
    cookie_secure: bool | None = None  # None -> auto (secure in prod)

    @property
    def is_prod(self) -> bool:
        return self.env.lower() in {"prod", "production"}

    @property
    def cookies_secure(self) -> bool:
        return self.is_prod if self.cookie_secure is None else self.cookie_secure


@lru_cache
def get_settings() -> Settings:
    return Settings()
