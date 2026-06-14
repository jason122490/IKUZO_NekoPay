# NekoPay 共用帳號 — 內部分帳管理網站

一群朋友共用同一個真實 **NekoPay**（白喵一番屋）街機錢包。真實帳號只是一個「共同池」，
無法分辨每筆是誰玩/誰儲。本網站是一個 **內部分帳帳本**：把共同池切分成每位成員的份額、
追蹤金錢(NT$)、並可與真實帳號對帳。

四個核心動作：**創建帳號 · 儲值(NT$→點數) · 投幣(消耗點數) · 轉點(成員間移轉，純內部)**。

> ⚠️ **安全**：請先輪換共用 NekoPay 密碼，並刪除 repo 根目錄的 `user_info` 明文檔
> （已被 `.gitignore` 排除）。共用帳密只放在 `.env`（不入版控），僅供伺服器端同步使用。

---

## 技術架構

- **FastAPI + SQLAlchemy 2.0 (async) + SQLite (WAL) + Alembic + Pydantic v2**
- **Auth**：自建 session（argon2 雜湊 + 伺服器端可撤銷 session + Secure/HttpOnly cookie + CSRF token + 角色 member/admin）。
  *（與計畫的 fastapi-users 不同——改用自建 session，較易測試且可控；安全屬性一致。）*
- **深度整合**：APScheduler（同進程）每 5 分鐘用 httpx（瀏覽器 UA）登入共用帳號、
  快照 `user_info` 餘額、去重 upsert `getPayHistory`。自動歸戶不可能 → 提供**輔助歸戶 + 對帳**。
- **分析/結算**：pandas/Python 計算每人投入、消費、餘額、淨額與「誰欠誰」最小轉帳。
- **前端**：Jinja2 server-rendered（登入/儀表板/管理）。

關鍵設計（去重、帳本不變式、對帳）詳見 `../.claude/plans/nekopay-python-robust-pixel.md`
與反向工程文件 `NekoPay-API.md` / `NekoPay-openapi.yaml`。

## 專案結構

```
app/
  main.py            FastAPI app + lifespan(scheduler/bootstrap) + middleware
  config.py db.py    settings + async engine/session
  security.py        auth dependencies (current member / admin / CSRF)
  models/            members, ledger_entries, real_transactions, snapshots, ...
  schemas.py         Pydantic request/response
  api/               auth, members, actions(儲值/投幣/轉點), admin, analytics
  web.py templates/  server-rendered pages
  services/          ledger, settlement, attribution, reconciliation, nekopay_client, ...
  sync/              dedup (★) + scheduler/poller
alembic/             migrations
tests/               34 tests
```

## 本機開發

```bash
python -m venv .venv
.venv/Scripts/python -m pip install -r requirements-dev.txt   # Windows
# Linux/mac: .venv/bin/python ...
cp .env.example .env        # 填入 SECRET_KEY、(可選)NEKOPAY 帳密、ADMIN_BOOTSTRAP_*
.venv/Scripts/python -m alembic upgrade head
.venv/Scripts/python -m uvicorn app.main:app --reload
# 開 http://127.0.0.1:8000 ，用 ADMIN_BOOTSTRAP_EMAIL/PASSWORD 登入
```

設定本機不連真實帳號：`RUN_SCHEDULER=false`。要連線真實帳號做對帳時設 `true` 並填 NEKOPAY 帳密。

## 測試

```bash
.venv/Scripts/python -m pytest -q     # 34 passing
```
重點：`test_dedup.py`（去重冪等/跨年/同分鐘重複）、`test_ledger.py`（轉點原子守恆/禁透支/idempotency/反向）、
`test_sync.py`（respx mock；token 刷新）、`test_settlement.py`、`test_attribution.py`、`test_api.py`（auth/CSRF/IDOR）。

## 部署（公開網際網路，自動 HTTPS）

```bash
cp .env.example .env        # 設正式 secrets + ENV=prod
export DOMAIN=nekopay.example.com
docker compose up -d --build
```
- **Caddy** 自動申請/續期 Let's Encrypt 憑證、HTTP→HTTPS、加安全標頭。
- **app** 啟動時 `alembic upgrade head` 後跑 uvicorn（單 worker，配合同進程排程）。
- SQLite 存於 `nekopay-data` volume（WAL）。多實例/高併發時改 Postgres（換 `DATABASE_URL`+`asyncpg`）。

### 備份

```bash
docker compose exec app python scripts/backup.py   # cron 每日；保留最近 14 份
```

## 安全重點

- 自建 session（可即時撤銷）；argon2 雜湊；Secure/HttpOnly/SameSite=Lax cookie；CSRF token。
- 角色強制於後端 dependency；成員只能讀自己的 ledger（防 IDOR）；無未認證的狀態變更端點。
- 共用 NekoPay 帳密只在 `.env`/環境變數；快取 token 可用 `SECRET_ENCRYPTION_KEY`(Fernet) 加密。
- `slowapi` 全域限流；安全標頭 + CSP；帳本 append-only（完整稽核）。
