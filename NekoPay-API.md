# 白喵一番屋 / NekoPay — API Reference

> Reconstructed from the public uni-app (DCloud) H5 client bundles at `https://shironekoya.net`
> via **static analysis** of the JavaScript chunks. `login/do_login` and `Nekopay/getPayHistory`
> were additionally **verified against the live server**; all other response shapes are
> best‑effort, inferred from how the client consumes each response.
>
> Generated 2026-06-14.

---

## 1. Overview

| | |
|---|---|
| **App** | 我的白喵一番屋 (`__UNI__C810CDD`), v1.0.0 — arcade membership + stored‑value wallet ("NekoPay") |
| **Frontend** | uni-app (Vue 2) compiled to H5, hash router, served behind Cloudflare |
| **Backend** | PHP, **ThinkPHP**-style routing: `/index/<Controller>/<action>` |
| **Base URL** | `https://shironekoya.net` (frontend and API share the origin; client reads it from `globalData.site_url`) |
| **Locale** | `zh-TW` |

---

## 2. Transport & conventions

### 2.1 HTTP method
The client calls every JSON endpoint with **`uni.request` and does not set `method`**, so the
client default (**GET**, params in the query string) is used. The ThinkPHP backend generally
accepts the same route over **GET or POST**. Verified: `do_login` and `getPayHistory` both
respond to `GET` with URL‑encoded params.

File upload (`common/upload`) is the exception — it is **`POST multipart/form-data`** via
`uni.uploadFile`.

### 2.2 Response envelope
All JSON endpoints return:

```jsonc
{
  "code": "1",      // status code — STRING, and its meaning is NOT consistent (see 2.4)
  "msg":  "…",      // human message, often shown directly to the user as a toast
  "data": { … }     // payload (object, array, or scalar) — absent/empty on some errors
}
```

### 2.3 Authentication
1. `POST /index/login/do_login` with `email` + `password` → returns `data.token` (32‑char string).
2. The client stores the whole login payload in local storage under the key `user`.
3. **Every authenticated call sends the token as a request parameter** `token` (in the body/query).
   There is **no `Authorization` header, no signature, and no nonce** — possession of the token
   string is full account access until it expires server‑side.

```
token = localStorage["user"].token
```

### 2.4 ⚠️ Success‑code convention is inconsistent
There is **no global rule** for what `code` means success — it differs per endpoint, and in one
case (`paycode_set`) even per call‑mode. Always check the specific endpoint below.

| Convention | Endpoints |
|---|---|
| **`code == 1` = success** | `do_login`, `login/reg`, `login/forget`, `login/getEmail`, `user/index`, `user/do_user_info`, `common/upload`, `index/get_site`, `index/get_time`, `index/ed_tongji`, `order/order_list`, `order/order_detail` |
| **`code == 0` = success** (`code != 0` ⇒ error) | most `nekopay/*` and `Nekopay/*`: `user_info`, `getPayHistory`, `getTicket`, `ticketReceive`, `link`, `unlink`, `lock`, `unlock`, `trans_card`, `trans_check`, `paycode_get`, `icon_get`, `icon_set`, `store`, `submit*Order*`, `door_info`, `event_1022`, `event_1031` |
| **multi‑state** | `paycode_set` (first‑set: `0`=ok / change: `1`=ok), `paycode_clear` (`0/1/2`), `submit*Order*` (`0`=ok, `200/201/3`=stock/points/token errors), `form2605Premium` (`0`=ok, `3`/`800`=state) |

---

## 3. Endpoint reference

Legend: 🔓 = no token required · 🔒 = token required.

### 3.1 Auth & account

#### 🔓 `do_login` — log in
```
GET /index/login/do_login?email=<email>&password=<password>
```
| Param | Req | Notes |
|---|---|---|
| `email` | ✓ | account email |
| `password` | ✓ | plaintext over HTTPS |

**Success:** `code == 1`. **Response:** `data.token` (string). *(verified live)*

#### 🔓 `getEmail` — send e‑mail verification code
```
GET /index/login/getEmail?email=<email>
```
Sends a one‑time `code` to the e‑mail. Used by register / forgot‑password / change‑password. **Success:** `code == 1`.

#### 🔓 `reg` — register
```
GET /index/login/reg?email=&code=&password=&repassword=
```
| Param | Notes |
|---|---|
| `email`, `password`, `repassword` | registration |
| `code` | e‑mail code from `getEmail` |

**Success:** `code == 1`.

#### 🔓 `forget` — reset password
```
GET /index/login/forget?email=&code=&password=&repassword=
```
Same params as `reg` (e‑mail code required). **Success:** `code == 1`. Also reused by the in‑app "change password" page.

#### 🔒 `user/index` — get profile / validate session
```
GET /index/user/index?token=<token>
```
Dual purpose: called on almost every page as an **auth gate** (if `code != 1` → redirect to login), and as the **profile fetch** on the edit page.
**Success:** `code == 1`. **Response `data`:** `{ nickname, pic, id_number, birth_date, email }` (`pic` is a path; prefix with base URL to display).

#### 🔒 `user/do_user_info` — update profile
```
GET /index/user/do_user_info?token=&nickname=&pic=&birth_date=
```
**Success:** `code == 1` (`code == 3` ⇒ token invalid).

#### 🔓 `common/upload` — image upload  *(multipart)*
```
POST /index/common/upload      (multipart/form-data)
  file = <binary>              field name: "file"
  user = "test"               (hard-coded formData in the client)
```
Returns a JSON **string** `{code,msg,data}`; on `code == 1`, `data` is the stored image path (then passed as `pic` to `do_user_info`).
> ⚠️ The client sends **no token** here and a hard‑coded `user:"test"` — effectively an unauthenticated upload. See §5.

### 3.2 Site / general

#### 🔒 `index/get_time` — server time + user counters
```
GET /index/index/get_time
```
**Success:** `code == 1`. **Response `data`:** `{ now_name, now_ymd, nickname, jinbi, dianshu, huizhang, liuyan, liuyan_all }` (gold/points/stamps/message counters).

#### 🔒 `index/ed_tongji` — log visit + summary counters
```
GET /index/index/ed_tongji?token=<token>
```
Statistics ("統計") ping fired on page loads; returns the same counter block `{ nickname, jinbi, dianshu, huizhang, liuyan, liuyan_all }`. **Success:** `code == 1`.

#### 🔓 `index/get_site` — site content/config by type
```
GET /index/index/get_site?type=<n>
```
Generic CMS fetch; `type` selects the section (observed `type=2`, `type=4`). **Success:** `code == 1`. **Response `data`:** section blob incl. `app_image` (+ page‑specific fields).

### 3.3 NekoPay card

#### 🔒 `nekopay/user_info` — wallet / card summary
```
GET /index/nekopay/user_info?token=<token>
```
**Success:** `code == 0` (`code != 0` ⇒ "Nekopay連線錯誤"). **Response `data`:**
`{ cardId, balance, status, type, vipName, vipNextValue, ticketPoint, transCard, isDoorCard, isPremium, event }`.
`cardId == 0` ⇒ no card linked yet.

#### 🔒 `nekopay/link` — bind a physical card
```
GET /index/nekopay/link?token=&cardIdFront=&cardIdBack=
```
| Param | Notes |
|---|---|
| `cardIdFront`, `cardIdBack` | the two halves printed on the card |

**Success:** `code == 0`.

#### 🔒 `nekopay/unlink` — unbind card
```
GET /index/nekopay/unlink?token=<token>
```
**Success:** `code == 0`.

#### 🔒 `nekopay/trans_card` — transfer to a new card
```
GET /index/nekopay/trans_card?token=&cardIdFront=&cardIdBack=
```
Migrates balance/membership to a replacement card. **Success:** `code == 0`.

#### 🔒 `nekopay/trans_check` — transfer status
```
GET /index/nekopay/trans_check?token=<token>
```
**Success:** `code == 0`. **Response `data`:** `{ cardId, transCard }`.

#### 🔒 `nekopay/lock` / `nekopay/unlock` — freeze / unfreeze card
```
GET /index/nekopay/lock?token=<token>
GET /index/nekopay/unlock?token=<token>
```
**Success:** `code == 0`.

### 3.4 Pay‑code (payment PIN)

> `payCode` is a **secret PIN**, not a QR/barcode — the app bundles no QR/barcode generator.
> `paycode_get` only reveals whether one is set; the value authorizes deductions at the terminal.

#### 🔒 `nekopay/paycode_get` — pay‑code status
```
GET /index/nekopay/paycode_get?token=<token>
```
**Success:** `code == 0`. **Response `data`:** `{ cardId, status, payCode, reqFlag }`.
`status`: `normal` | `locked` | other (⇒ "卡片停用"). `payCode` present ⇒ "已啟用", absent ⇒ "未設定". `reqFlag` ⇒ a charge is pending approval.

#### 🔒 `nekopay/paycode_set` — set / change PIN
```
GET /index/nekopay/paycode_set?token=&paycodeSet=[&paycodeCurrent=]
```
| Mode | Params | Success |
|---|---|---|
| First‑time set | `paycodeSet` | `code == 0` |
| Change | `paycodeCurrent` + `paycodeSet` | `code == 1` (`2` ⇒ wrong current) |

#### 🔒 `nekopay/paycode_clear` — remove PIN
```
GET /index/nekopay/paycode_clear?token=&paycodeCurrent=
```
Multi‑state `code` `0/1/2` (`2` ⇒ wrong current PIN).

### 3.5 Points store & spending

#### 🔒 `nekopay/getPayHistory` — top‑up & spend ledger
```
GET /index/Nekopay/getPayHistory?token=<token>
```
**Success:** `code == 0` (`msg: "Success"`). *(verified live)* **Response `data`:**
```jsonc
{
  "topup": [ { "time": {"date":"06/10","time":"18:07"}, "name":"竹喵店", "value":33 } ],
  "pay":   [ { "time": {"date":"06/10","time":"20:47"}, "name":"竹喵店 - Chunithm", "value":3, "type":"point" } ]
}
```
- `topup[].name` = **shop**; `pay[].name` = **"shop - machine/game"**; `value` = points (±).
- `pay[].type`: `point` | `ticket` (selects the row icon).
- `time` has **no year** and is **minute** resolution. Returns **recent records only** — no pagination param; the full ledger is not reachable here.
- No per‑cabinet identifier (multiple identical machines collapse to the same `name`).

#### 🔒 `nekopay/store` — points‑shop catalog / item
```
GET /index/nekopay/store?token=<token>[&uuid=<itemUuid>]
```
No `uuid` ⇒ catalog list; with `uuid` ⇒ single item detail. **Success:** `code == 0` (`100` ⇒ card not linked). **Response `data`:** `{ store, point, extra }` (`point` = user's balance).

#### 🔒 `nekopay/submitStoreOrder` — redeem a store item
```
GET /index/nekopay/submitStoreOrder?token=&uuid=&extra=&address=
```
`address` is built as `"<address> - <delivery>"`. **Success:** `code == 0`; `200/201/3` ⇒ out‑of‑stock / insufficient points / token error.

### 3.6 Tickets

#### 🔒 `Nekopay/getTicket` — list tickets
```
GET /index/Nekopay/getTicket?token=<token>
```
**Success:** `code == 0`. **Response `data`:** `{ newTicket[], receivedTicket[], outdatedTicket[], availablePoint }`.

#### 🔒 `Nekopay/ticketReceive` — claim a ticket
```
GET /index/Nekopay/ticketReceive?token=&ticket=<ticketId>
```
**Success:** `code == 0`.

### 3.7 Icons (card avatar)

#### 🔒 `nekopay/icon_get` — available icons
```
GET /index/nekopay/icon_get?token=<token>
```
**Success:** `code == 0`. **Response `data`:** `{ currentId, currentName, iconList[] }`.

#### 🔒 `nekopay/icon_set` — choose icon
```
GET /index/nekopay/icon_set?token=&iconId=<int>
```
**Success:** `code == 0`. **Response `data`:** `{ result }`.

### 3.8 Door access (門禁)

#### 🔒 `nekopay/door_info` — door‑card status
```
GET /index/nekopay/door_info?token=<token>
```
**Success:** `code == 0`. **Response `data`:** `{ cardId, doorCode, phone, reqFlag, status }`.

#### 🔒 `nekopay/door_sms` — send phone verification SMS
```
GET /index/nekopay/door_sms?token=&phone=<phone>
```

#### 🔒 `nekopay/door_submit` — verify phone & enable door access
```
GET /index/nekopay/door_submit?token=&phone=&code=<smsCode>
```

### 3.9 Events / exchanges

#### 🔒 `nekopay/event_1031` / `nekopay/event_1022` — event exchange
```
GET /index/nekopay/event_1031?token=&uuid=<itemUuid>
GET /index/nekopay/event_1022?token=&uuid=<itemUuid>   (premium variant)
```
**Success:** `code == 0` (also inner `data.code`). **Response `data`:** `{ code, goods, point }`.

#### 🔒 `nekopay/submitOrder` / `nekopay/submitOrderPremium` — place event order
```
GET /index/nekopay/submitOrder?token=&uuid=&address=
GET /index/nekopay/submitOrderPremium?token=&uuid=&extra=&address=
```
**Success:** `code == 0`; `200/201/3` ⇒ stock / points / token errors.

#### 🔒 `nekopay/form2605Premium` — event 1032 premium form
```
GET /index/nekopay/form2605Premium?token=&store=&item=
```
**Response `data`:** `{ isFilled, isKing, isPremium }`. Codes: `0` ok, `3`/`800` = state (already filled / not premium).

### 3.10 Orders

#### 🔒 `order/order_list` — paginated order history
```
GET /index/order/order_list?token=&status=<filter>&page=<n>
```
**Success:** `code == 1`. **Response `data`:** `{ list[], all_page }` (`all_page` = total pages). The **only** paginated endpoint.

#### `order/order_detail` — single order
```
GET /index/order/order_detail?id=<orderId>
```
**Success:** `code == 1`.
> ⚠️ Takes only `id`, **no token** — potential IDOR (see §5).

---

## 4. Quick index

| # | Endpoint | Auth | Success | Key request params |
|---|---|---|---|---|
| 1 | `login/do_login` | 🔓 | `1` | email, password |
| 2 | `login/getEmail` | 🔓 | `1` | email |
| 3 | `login/reg` | 🔓 | `1` | email, code, password, repassword |
| 4 | `login/forget` | 🔓 | `1` | email, code, password, repassword |
| 5 | `user/index` | 🔒 | `1` | token |
| 6 | `user/do_user_info` | 🔒 | `1` | token, nickname, pic, birth_date |
| 7 | `common/upload` | 🔓 | `1` | file (multipart) |
| 8 | `index/get_time` | 🔒 | `1` | — |
| 9 | `index/ed_tongji` | 🔒 | `1` | token |
| 10 | `index/get_site` | 🔓 | `1` | type |
| 11 | `nekopay/user_info` | 🔒 | `0` | token |
| 12 | `nekopay/link` | 🔒 | `0` | token, cardIdFront, cardIdBack |
| 13 | `nekopay/unlink` | 🔒 | `0` | token |
| 14 | `nekopay/trans_card` | 🔒 | `0` | token, cardIdFront, cardIdBack |
| 15 | `nekopay/trans_check` | 🔒 | `0` | token |
| 16 | `nekopay/lock` | 🔒 | `0` | token |
| 17 | `nekopay/unlock` | 🔒 | `0` | token |
| 18 | `nekopay/paycode_get` | 🔒 | `0` | token |
| 19 | `nekopay/paycode_set` | 🔒 | `0`/`1` | token, paycodeSet, [paycodeCurrent] |
| 20 | `nekopay/paycode_clear` | 🔒 | `0/1/2` | token, paycodeCurrent |
| 21 | `Nekopay/getPayHistory` | 🔒 | `0` | token |
| 22 | `nekopay/store` | 🔒 | `0` | token, [uuid] |
| 23 | `nekopay/submitStoreOrder` | 🔒 | `0` | token, uuid, extra, address |
| 24 | `Nekopay/getTicket` | 🔒 | `0` | token |
| 25 | `Nekopay/ticketReceive` | 🔒 | `0` | token, ticket |
| 26 | `nekopay/icon_get` | 🔒 | `0` | token |
| 27 | `nekopay/icon_set` | 🔒 | `0` | token, iconId |
| 28 | `nekopay/door_info` | 🔒 | `0` | token |
| 29 | `nekopay/door_sms` | 🔒 | — | token, phone |
| 30 | `nekopay/door_submit` | 🔒 | — | token, phone, code |
| 31 | `nekopay/event_1031` | 🔒 | `0` | token, uuid |
| 32 | `nekopay/event_1022` | 🔒 | `0` | token, uuid |
| 33 | `nekopay/submitOrder` | 🔒 | `0` | token, uuid, address |
| 34 | `nekopay/submitOrderPremium` | 🔒 | `0` | token, uuid, extra, address |
| 35 | `nekopay/form2605Premium` | 🔒 | `0` | token, store, item |
| 36 | `order/order_list` | 🔒 | `1` | token, status, page |
| 37 | `order/order_detail` | 🔓 | `1` | id |

---

## 5. Notes & observations (for the operator)

These are robustness/security notes worth reviewing on the backend — they are not exploited here.

1. **Token in the query string.** Because calls default to GET, the `token` rides in the URL,
   so it can land in server access logs, proxies, and browser history. Consider an
   `Authorization` header and/or forcing POST for authenticated calls.
2. **Inconsistent `code` semantics.** Success is `1` for auth/order endpoints but `0` for most
   `nekopay/*`, and `paycode_set` flips between `0` and `1` by mode. A single convention would
   remove a whole class of client bugs.
3. **`order/order_detail` takes only `id`, no token.** If the server doesn't separately verify
   ownership, this is an IDOR (any order viewable by guessing/enumerating `id`).
4. **`common/upload` sends no token** and a hard‑coded `formData:{ user:"test" }` — looks like
   leftover scaffolding; an unauthenticated upload endpoint is worth locking down.
5. **`payCode` is a static secret PIN.** There's no rotation/expiry visible client‑side; a
   leaked PIN + `cardId` is reusable. A rotating/expiring code (and never returning the raw
   `payCode` to the client) would harden payments.
6. **`getPayHistory` is truncated, unpaginated, and minute‑resolution with no year**, and carries
   no per‑cabinet identifier. If per‑machine analytics or full history are needed, extend the
   `pay[]` record server‑side (e.g. add `terminalId`/`cabinetNo`, full `datetime`, and a `page` param).

---

## 6. Example flow (placeholders — do not commit real credentials)

```bash
BASE="https://shironekoya.net"

# 1) Log in → token  (success: code == 1)
TOKEN=$(curl -s --get "$BASE/index/login/do_login" \
  --data-urlencode "email=YOUR_EMAIL" \
  --data-urlencode "password=YOUR_PASSWORD" \
  | sed -n 's/.*"token":"\([^"]*\)".*/\1/p')

# 2) Wallet summary  (success: code == 0)
curl -s --get "$BASE/index/nekopay/user_info" --data-urlencode "token=$TOKEN"

# 3) Pay history  (success: code == 0)
curl -s --get "$BASE/index/Nekopay/getPayHistory" --data-urlencode "token=$TOKEN"
```

> Use only against accounts you own/operate. The token grants full account access while valid.
