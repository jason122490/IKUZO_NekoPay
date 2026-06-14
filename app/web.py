"""Server-rendered HTML pages (Jinja2). Actions POST to the JSON API via fetch."""
from __future__ import annotations

from datetime import timedelta

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.util.time import utcnow

EDIT_WINDOW = timedelta(minutes=30)

from app.config import get_settings
from app.db import get_session
from app.models.enums import AttributionStatus, ClaimStatus
from app.models.ledger import AttributionClaim, LedgerEntry
from app.models.real import AccountSnapshot, RealTransaction
from app.models.user import Member
from app.services import auth_service, config_service, ledger_service
from app.vip import VIP_TIERS, next_tier as vip_next_tier
from app.services.reconciliation import reconcile_report
from app.services.settlement import compute_positions, settle

router = APIRouter(tags=["web"])
templates = Jinja2Templates(directory="app/templates")
settings = get_settings()


async def _current(request: Request, session: AsyncSession):
    token = request.cookies.get(settings.session_cookie_name, "")
    us = await auth_service.get_session(session, token)
    if us is None:
        return None, None
    member = await session.get(Member, us.member_id)
    if member is None or not member.is_active:
        return None, None
    return member, us.csrf_token


@router.get("/", response_class=HTMLResponse)
async def index(request: Request, session: AsyncSession = Depends(get_session)):
    member, _ = await _current(request, session)
    return RedirectResponse("/dashboard" if member else "/login", status_code=303)


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    return templates.TemplateResponse(request, "login.html", {"error": None})


@router.post("/login", response_class=HTMLResponse)
async def login_submit(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    session: AsyncSession = Depends(get_session),
):
    member = await auth_service.authenticate(session, email, password)
    if member is None:
        return templates.TemplateResponse(
            request, "login.html", {"error": "帳號或密碼錯誤"}, status_code=401
        )
    us = await auth_service.create_session(session, member.id, settings.session_ttl_hours)
    resp = RedirectResponse("/dashboard", status_code=303)
    resp.set_cookie(
        settings.session_cookie_name, us.token,
        max_age=settings.session_ttl_hours * 3600,
        httponly=True, secure=settings.cookies_secure, samesite="lax",
    )
    return resp


@router.post("/logout")
async def logout(request: Request, session: AsyncSession = Depends(get_session)):
    token = request.cookies.get(settings.session_cookie_name, "")
    if token:
        await auth_service.delete_session(session, token)
    resp = RedirectResponse("/login", status_code=303)
    resp.delete_cookie(settings.session_cookie_name)
    return resp


@router.get("/dashboard", response_class=HTMLResponse)
async def dashboard(request: Request, session: AsyncSession = Depends(get_session)):
    member, csrf = await _current(request, session)
    if member is None:
        return RedirectResponse("/login", status_code=303)

    members = list(
        (await session.execute(select(Member).where(Member.is_active.is_(True))
                               .order_by(Member.display_name))).scalars()
    )
    rate = await config_service.get_rate(session, settings.default_rate_nt_per_point)
    positions = await compute_positions(session, rate)
    txns = settle(positions)
    my_balance = await ledger_service.get_balance(session, member.id)
    entries = list(
        (await session.execute(
            select(LedgerEntry).where(LedgerEntry.member_id == member.id)
            .order_by(LedgerEntry.created_at.desc()).limit(15)
        )).scalars()
    )
    now = utcnow()
    is_admin = member.role == "admin"
    recent_rows = [
        {
            "e": e,
            "can_modify": is_admin or (now - e.created_at) <= EDIT_WINDOW,
            "is_transfer": e.transfer_group_id is not None,
        }
        for e in entries
    ]
    recon = await reconcile_report(session) if is_admin else None

    snap = (await session.execute(
        select(AccountSnapshot).order_by(AccountSnapshot.captured_at.desc()).limit(1)
    )).scalar_one_or_none()
    vip = None
    if snap is not None and snap.vip_name:
        nxt = vip_next_tier(snap.vip_name)
        vip = {
            "name": snap.vip_name,
            "is_premium": snap.is_premium,
            "next_value": snap.vip_next_value,
            "next_name": nxt["name"] if nxt else None,
        }

    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {
            "member": member, "csrf": csrf, "members": members,
            "positions": positions, "txns": txns, "rate": rate,
            "my_balance": my_balance, "recent_rows": recent_rows, "recon": recon,
            "vip": vip,
        },
    )


@router.get("/vip", response_class=HTMLResponse)
async def vip_page(request: Request, session: AsyncSession = Depends(get_session)):
    member, csrf = await _current(request, session)
    if member is None:
        return RedirectResponse("/login", status_code=303)
    snap = (await session.execute(
        select(AccountSnapshot).order_by(AccountSnapshot.captured_at.desc()).limit(1)
    )).scalar_one_or_none()
    current = snap.vip_name if snap else None
    return templates.TemplateResponse(
        request, "vip.html",
        {"member": member, "csrf": csrf, "tiers": VIP_TIERS, "current": current},
    )


@router.get("/admin", response_class=HTMLResponse)
async def admin_page(request: Request, session: AsyncSession = Depends(get_session)):
    member, csrf = await _current(request, session)
    if member is None or member.role != "admin":
        return RedirectResponse("/dashboard" if member else "/login", status_code=303)

    unattributed = list(
        (await session.execute(
            select(RealTransaction)
            .where(RealTransaction.attribution_status == AttributionStatus.UNATTRIBUTED.value)
            .order_by(RealTransaction.occurred_at.desc()).limit(100)
        )).scalars()
    )
    claims = list(
        (await session.execute(
            select(AttributionClaim).where(AttributionClaim.status == ClaimStatus.PENDING.value)
        )).scalars()
    )
    members = list(
        (await session.execute(select(Member).where(Member.is_active.is_(True)))).scalars()
    )
    recon = await reconcile_report(session)
    rate = await config_service.get_rate(session, settings.default_rate_nt_per_point)
    return templates.TemplateResponse(
        request,
        "admin.html",
        {
            "member": member, "csrf": csrf, "unattributed": unattributed,
            "claims": claims, "members": members, "recon": recon, "rate": rate,
        },
    )


@router.get("/admin/members", response_class=HTMLResponse)
async def admin_members(request: Request, session: AsyncSession = Depends(get_session)):
    member, csrf = await _current(request, session)
    if member is None or member.role != "admin":
        return RedirectResponse("/dashboard" if member else "/login", status_code=303)
    rows = list(
        (await session.execute(select(Member).order_by(Member.display_name))).scalars()
    )
    data = [
        {
            "m": m,
            "balance": await ledger_service.get_balance(session, m.id),
            "money": await ledger_service.get_money_contributed(session, m.id),
        }
        for m in rows
    ]
    return templates.TemplateResponse(
        request, "members.html", {"member": member, "csrf": csrf, "rows": data}
    )


@router.get("/admin/members/{member_id}", response_class=HTMLResponse)
async def admin_member_detail(
    request: Request, member_id: int, session: AsyncSession = Depends(get_session)
):
    member, csrf = await _current(request, session)
    if member is None or member.role != "admin":
        return RedirectResponse("/dashboard" if member else "/login", status_code=303)
    target = await session.get(Member, member_id)
    if target is None:
        return RedirectResponse("/admin/members", status_code=303)
    entries = list(
        (await session.execute(
            select(LedgerEntry).where(LedgerEntry.member_id == member_id)
            .order_by(LedgerEntry.created_at.desc()).limit(200)
        )).scalars()
    )
    # admin views these, so every row is modifiable (no time limit)
    rows = [{"e": e, "is_transfer": e.transfer_group_id is not None} for e in entries]
    return templates.TemplateResponse(
        request,
        "member_detail.html",
        {
            "member": member, "csrf": csrf, "target": target, "rows": rows,
            "balance": await ledger_service.get_balance(session, member_id),
            "money": await ledger_service.get_money_contributed(session, member_id),
        },
    )
