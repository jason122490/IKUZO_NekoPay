"""Server-rendered HTML pages (Jinja2). Actions POST to the JSON API via fetch."""
from __future__ import annotations

import csv
import io
from decimal import ROUND_HALF_UP, Decimal

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.util.time import local_to_utc, to_local, utcnow

from app.config import get_settings
from app.db import get_session
from app.models.enums import AttributionStatus, EntryType
from app.models.ledger import LedgerEntry
from app.models.real import AccountSnapshot, RealTransaction, SyncRun
from app.models.user import Member
from app.services import auth_service, config_service, ledger_service
from app.vip import (
    BONUS_MIN_TOPUP,
    VIP_TIERS,
    bonus_pct_for,
    next_tier as vip_next_tier,
)
from app.services.reconciliation import reconcile_report
from app.services.settlement import compute_positions

router = APIRouter(tags=["web"])
templates = Jinja2Templates(directory="app/templates")
# bump to force browsers to re-fetch static CSS/JS after changes
templates.env.globals["asset_v"] = "17"
# Chinese labels for enum values shown in the UI
templates.env.globals["ENTRY_LABELS"] = {
    "TOPUP": "儲值", "PLAY": "投幣", "TRANSFER_IN": "轉入",
    "TRANSFER_OUT": "轉出", "ADJUSTMENT": "調整",
}
templates.env.globals["KIND_LABELS"] = {"topup": "儲值", "pay": "消費"}
templates.env.globals["ATTR_LABELS"] = {
    "unattributed": "未歸戶", "attributed": "已歸戶", "ignored": "已忽略",
}
templates.env.globals["RUN_LABELS"] = {
    "ok": "成功", "auth_failed": "認證失敗",
    "transport_failed": "連線失敗", "partial": "部分失敗",
}
settings = get_settings()


def _localdt(dt, fmt: str = "%m/%d %H:%M") -> str:
    """Render a stored naive-UTC datetime in the app timezone (e.g. Taipei)."""
    if dt is None:
        return ""
    return to_local(dt, settings.app_timezone).strftime(fmt)


templates.env.filters["localdt"] = _localdt


def _entry_rows(entries):
    """View-model rows for the ledger table (dashboard + full records page)."""
    return [
        {
            "e": e,
            "can_modify": True,  # members may edit/delete their own records anytime
            "is_transfer": e.transfer_group_id is not None,
            # 歸戶: a manual top-up/play not yet linked to a real transaction
            "can_attribute": (
                e.transfer_group_id is None
                and e.source_real_txn_id is None
                and e.entry_type in (EntryType.TOPUP.value, EntryType.PLAY.value)
            ),
        }
        for e in entries
    ]


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
    username: str = Form(...),
    password: str = Form(...),
    session: AsyncSession = Depends(get_session),
):
    member = await auth_service.authenticate(session, username, password)
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
    snap = (await session.execute(
        select(AccountSnapshot).order_by(AccountSnapshot.captured_at.desc()).limit(1)
    )).scalar_one_or_none()
    vip_bonus_pct = bonus_pct_for(snap.vip_name) if snap else 0
    positions = await compute_positions(session, rate)
    my_balance = await ledger_service.get_balance(session, member.id)

    # daily consumption (points spent on PLAY), last 30 days, for the bar chart
    spend_rows = (await session.execute(
        select(
            func.date(LedgerEntry.created_at),
            func.coalesce(func.sum(-LedgerEntry.points_delta), 0),
        )
        .where(
            LedgerEntry.member_id == member.id,
            LedgerEntry.entry_type == EntryType.PLAY.value,
        )
        .group_by(func.date(LedgerEntry.created_at))
        .order_by(func.date(LedgerEntry.created_at))
    )).all()
    spend_rows = spend_rows[-30:]
    spend_labels = [str(r[0]) for r in spend_rows]
    spend_values = [int(r[1]) for r in spend_rows]
    total_spent = sum(spend_values)

    # personal exchange rate: NT$ actually paid per point, from this member's
    # own top-ups (money / points). Falls back to the global rate if none yet.
    topup_money, topup_points = (await session.execute(
        select(
            func.coalesce(func.sum(LedgerEntry.money_nt), 0),
            func.coalesce(func.sum(LedgerEntry.points_delta), 0),
        ).where(
            LedgerEntry.member_id == member.id,
            LedgerEntry.money_nt.is_not(None),
        )
    )).one()
    has_personal_rate = int(topup_points) > 0
    my_rate = (Decimal(str(topup_money)) / int(topup_points)) if has_personal_rate else rate
    my_balance_nt = (Decimal(my_balance) * my_rate).quantize(Decimal("1"), ROUND_HALF_UP)
    total_spent_nt = (Decimal(total_spent) * my_rate).quantize(Decimal("1"), ROUND_HALF_UP)
    my_rate_display = my_rate.quantize(Decimal("0.01"))

    entries = list(
        (await session.execute(
            select(LedgerEntry).where(LedgerEntry.member_id == member.id)
            .order_by(LedgerEntry.created_at.desc()).limit(15)
        )).scalars()
    )
    is_admin = member.role == "admin"
    recent_rows = _entry_rows(entries)
    recon = await reconcile_report(session) if is_admin else None

    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {
            "member": member, "csrf": csrf, "members": members,
            "positions": positions, "rate": rate,
            "my_balance": my_balance, "recent_rows": recent_rows, "recon": recon,
            "spend_labels": spend_labels, "spend_values": spend_values,
            "total_spent": total_spent, "vip_bonus_pct": vip_bonus_pct,
            "my_balance_nt": my_balance_nt, "total_spent_nt": total_spent_nt,
            "my_rate_display": my_rate_display, "has_personal_rate": has_personal_rate,
            "bonus_min_topup": BONUS_MIN_TOPUP,
        },
    )


@router.get("/records", response_class=HTMLResponse)
async def my_records(
    request: Request,
    kind: str = "",
    attr: str = "",
    sort: str = "time",
    session: AsyncSession = Depends(get_session),
):
    member, csrf = await _current(request, session)
    if member is None:
        return RedirectResponse("/login", status_code=303)
    stmt = select(LedgerEntry).where(LedgerEntry.member_id == member.id)
    if kind == "topup":
        stmt = stmt.where(LedgerEntry.entry_type == EntryType.TOPUP.value)
    elif kind == "play":
        stmt = stmt.where(LedgerEntry.entry_type == EntryType.PLAY.value)
    elif kind == "transfer":
        stmt = stmt.where(LedgerEntry.entry_type.in_(
            [EntryType.TRANSFER_IN.value, EntryType.TRANSFER_OUT.value]))
    elif kind == "adjustment":
        stmt = stmt.where(LedgerEntry.entry_type == EntryType.ADJUSTMENT.value)
    if attr == "yes":
        stmt = stmt.where(LedgerEntry.source_real_txn_id.is_not(None))
    elif attr == "no":
        stmt = stmt.where(LedgerEntry.source_real_txn_id.is_(None))
    newest_first = LedgerEntry.created_at.desc()
    if sort == "type":
        stmt = stmt.order_by(LedgerEntry.entry_type, newest_first)
    elif sort == "attributed":  # 已歸戶 first, then by time
        stmt = stmt.order_by(LedgerEntry.source_real_txn_id.is_(None), newest_first)
    else:  # time
        sort = "time"
        stmt = stmt.order_by(newest_first)
    entries = list((await session.execute(stmt)).scalars())
    return templates.TemplateResponse(
        request,
        "records.html",
        {"member": member, "csrf": csrf, "rows": _entry_rows(entries),
         "kind": kind, "attr": attr, "sort": sort, "total": len(entries)},
    )


@router.get("/vip", response_class=HTMLResponse)
async def vip_page(request: Request, session: AsyncSession = Depends(get_session)):
    member, csrf = await _current(request, session)
    if member is None:
        return RedirectResponse("/login", status_code=303)
    snap = (await session.execute(
        select(AccountSnapshot).order_by(AccountSnapshot.captured_at.desc()).limit(1)
    )).scalar_one_or_none()
    vip = None
    if snap is not None and snap.vip_name:
        nxt = vip_next_tier(snap.vip_name)
        remaining = None
        if nxt and snap.vip_next_value is not None and snap.vip_cumulative is not None:
            remaining = max(0, int(snap.vip_next_value) - int(snap.vip_cumulative))
        vip = {
            "name": snap.vip_name,
            "is_premium": snap.is_premium,
            "next_value": snap.vip_next_value,
            "next_name": nxt["name"] if nxt else None,
            "cumulative": snap.vip_cumulative,
            "remaining": remaining,
        }
    return templates.TemplateResponse(
        request, "vip.html",
        {
            "member": member, "csrf": csrf, "tiers": VIP_TIERS,
            "current": snap.vip_name if snap else None, "vip": vip,
        },
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
    members = list(
        (await session.execute(select(Member).where(Member.is_active.is_(True)))).scalars()
    )
    recon = await reconcile_report(session)
    rate = await config_service.get_rate(session, settings.default_rate_nt_per_point)
    sync_since = await config_service.get_sync_since(session)
    return templates.TemplateResponse(
        request,
        "admin.html",
        {
            "member": member, "csrf": csrf, "unattributed": unattributed,
            "members": members, "recon": recon, "rate": rate,
            "sync_since": sync_since,
        },
    )


@router.get("/admin/history", response_class=HTMLResponse)
async def admin_history(
    request: Request,
    kind: str = "",
    status: str = "",
    session: AsyncSession = Depends(get_session),
):
    member, csrf = await _current(request, session)
    if member is None or member.role != "admin":
        return RedirectResponse("/dashboard" if member else "/login", status_code=303)

    stmt = select(RealTransaction).order_by(RealTransaction.occurred_at.desc())
    if kind in ("topup", "pay"):
        stmt = stmt.where(RealTransaction.kind == kind)
    if status in ("unattributed", "attributed", "ignored"):
        stmt = stmt.where(RealTransaction.attribution_status == status)
    rows = list((await session.execute(stmt.limit(500))).scalars())

    total = int((await session.execute(
        select(func.count()).select_from(RealTransaction))).scalar_one())
    span = (await session.execute(select(
        func.min(RealTransaction.occurred_at),
        func.max(RealTransaction.occurred_at)))).one()
    members_map = {
        m.id: m.display_name
        for m in (await session.execute(select(Member))).scalars()
    }
    last_run = (await session.execute(
        select(SyncRun).order_by(SyncRun.started_at.desc()).limit(1)
    )).scalar_one_or_none()
    last_age = (
        int((utcnow() - last_run.finished_at).total_seconds())
        if last_run and last_run.finished_at else None
    )
    recent_runs = list((await session.execute(
        select(SyncRun).order_by(SyncRun.id.desc()).limit(8))).scalars())

    return templates.TemplateResponse(
        request, "history.html",
        {
            "member": member, "csrf": csrf, "rows": rows, "shown": len(rows),
            "total": total, "span": span, "kind": kind, "status": status,
            "members_map": members_map, "last_run": last_run, "last_age": last_age,
            "recent_runs": recent_runs,
        },
    )


@router.get("/admin/history.csv")
async def admin_history_csv(
    request: Request, session: AsyncSession = Depends(get_session)
):
    member, _ = await _current(request, session)
    if member is None or member.role != "admin":
        return RedirectResponse("/dashboard" if member else "/login", status_code=303)
    rows = list((await session.execute(
        select(RealTransaction).order_by(RealTransaction.occurred_at.desc())
    )).scalars())
    members_map = {
        m.id: m.display_name
        for m in (await session.execute(select(Member))).scalars()
    }
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["occurred_at", "kind", "shop", "machine", "value", "pay_type",
                "attribution_status", "attributed_member", "first_seen_at",
                "last_seen_at"])
    for r in rows:
        w.writerow([
            _localdt(r.occurred_at, "%Y-%m-%d %H:%M:%S"),
            r.kind, r.shop, r.machine or "", r.value, r.pay_type or "",
            r.attribution_status,
            members_map.get(r.attributed_member_id, "") if r.attributed_member_id else "",
            _localdt(r.first_seen_at, "%Y-%m-%d %H:%M:%S"),
            _localdt(r.last_seen_at, "%Y-%m-%d %H:%M:%S"),
        ])
    return Response(
        content="﻿" + buf.getvalue(),  # BOM so Excel reads UTF-8 (Chinese)
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": "attachment; filename=nekopay_history.csv"},
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
