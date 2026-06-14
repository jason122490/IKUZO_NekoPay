"""FastAPI auth dependencies: current member, admin gate, CSRF."""
from __future__ import annotations

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db import get_session
from app.models.enums import Role
from app.models.user import Member
from app.services.auth_service import get_session as get_user_session

settings = get_settings()


async def _current(
    request: Request, session: AsyncSession
) -> tuple[Member, str] | None:
    token = request.cookies.get(settings.session_cookie_name, "")
    us = await get_user_session(session, token)
    if us is None:
        return None
    member = await session.get(Member, us.member_id)
    if member is None or not member.is_active:
        return None
    return member, us.csrf_token


async def get_current_member(
    request: Request, session: AsyncSession = Depends(get_session)
) -> Member:
    result = await _current(request, session)
    if result is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="not authenticated"
        )
    member, _ = result
    request.state.member = member
    return member


async def get_optional_member(
    request: Request, session: AsyncSession = Depends(get_session)
) -> Member | None:
    result = await _current(request, session)
    if result is None:
        return None
    member, _ = result
    request.state.member = member
    return member


async def require_admin(
    member: Member = Depends(get_current_member),
) -> Member:
    if member.role != Role.ADMIN.value:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="admin only"
        )
    return member


async def verify_csrf(
    request: Request, session: AsyncSession = Depends(get_session)
) -> None:
    """Synchronizer-token CSRF check for state-changing requests.

    The token is read from the X-CSRF-Token header or a `csrf_token` form field
    and compared to the value bound to the current session.
    """
    token = request.cookies.get(settings.session_cookie_name, "")
    us = await get_user_session(session, token)
    if us is None:
        raise HTTPException(status_code=401, detail="not authenticated")
    submitted = request.headers.get("X-CSRF-Token")
    if submitted is None:
        form = await request.form()
        submitted = form.get("csrf_token")
    if not submitted or submitted != us.csrf_token:
        raise HTTPException(status_code=403, detail="CSRF token invalid")
