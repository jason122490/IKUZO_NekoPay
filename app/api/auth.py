"""Authentication endpoints (cookie session)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db import get_session
from app.models.user import Member
from app.schemas import LoginIn, LoginOut, MemberOut, MessageOut
from app.security import get_current_member
from app.services import auth_service

router = APIRouter(prefix="/api/auth", tags=["auth"])
settings = get_settings()


def _set_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        key=settings.session_cookie_name,
        value=token,
        max_age=settings.session_ttl_hours * 3600,
        httponly=True,
        secure=settings.cookies_secure,
        samesite="lax",
    )


@router.post("/login", response_model=LoginOut)
async def login(
    payload: LoginIn,
    response: Response,
    session: AsyncSession = Depends(get_session),
) -> LoginOut:
    member = await auth_service.authenticate(session, payload.email, payload.password)
    if member is None:
        raise HTTPException(status_code=401, detail="invalid email or password")
    us = await auth_service.create_session(
        session, member.id, settings.session_ttl_hours
    )
    _set_cookie(response, us.token)
    return LoginOut(member=MemberOut.model_validate(member), csrf_token=us.csrf_token)


@router.post("/logout", response_model=MessageOut)
async def logout(
    request: Request,
    response: Response,
    session: AsyncSession = Depends(get_session),
) -> MessageOut:
    token = request.cookies.get(settings.session_cookie_name, "")
    if token:
        await auth_service.delete_session(session, token)
    response.delete_cookie(settings.session_cookie_name)
    return MessageOut(detail="logged out")


@router.get("/me", response_model=MemberOut)
async def me(member: Member = Depends(get_current_member)) -> MemberOut:
    return MemberOut.model_validate(member)
