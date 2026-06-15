// Minimal JSON+CSRF helper; actions post to the JSON API then reload.
window.NK = {
  async post(url, body) {
    const r = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-CSRF-Token": window.CSRF },
      body: JSON.stringify(body || {}),
    });
    let data = null;
    try { data = await r.json(); } catch (e) {}
    if (!r.ok) { alert((data && data.detail) || ("錯誤 " + r.status)); return null; }
    return data;
  },
  async del(url) {
    const r = await fetch(url, { method: "DELETE", headers: { "X-CSRF-Token": window.CSRF } });
    let data = null;
    try { data = await r.json(); } catch (e) {}
    if (!r.ok) { alert((data && data.detail) || ("錯誤 " + r.status)); return null; }
    return data;
  },
  reload() { location.reload(); },
};

// Nav links scroll sideways when they overflow. Desktop has no horizontal
// wheel, so map vertical wheel -> horizontal; and toggle the edge-fade classes
// so a fade only shows on a side that still has hidden content.
(function () {
  const el = document.querySelector(".nav nav");
  if (!el) return;
  function updateFades() {
    // 2px slack: browsers leave a sub-pixel gap at the extremes, so a tight
    // threshold would keep the right fade on even when scrolled fully right.
    const remaining = el.scrollWidth - el.clientWidth - el.scrollLeft;
    el.classList.toggle("can-left", el.scrollLeft > 2);
    el.classList.toggle("can-right", remaining > 2);
  }
  el.addEventListener("scroll", updateFades, { passive: true });
  window.addEventListener("resize", updateFades);
  el.addEventListener("wheel", (e) => {
    if (e.deltaY === 0) return;                       // let real horizontal scroll pass
    if (el.scrollWidth <= el.clientWidth) return;     // nothing hidden -> nothing to do
    el.scrollLeft += e.deltaY;
    e.preventDefault();                               // don't also scroll the page
  }, { passive: false });
  updateFades();
})();

function val(id) { return document.getElementById(id).value; }

// datetime-local helpers for the optional 指定時間 fields
function nowLocalValue() {
  const d = new Date(), pad = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}` +
         `T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}
function timeVal(id) {
  const el = document.getElementById(id);
  return el && el.value ? { occurred_at: el.value } : {};
}

// Reliable in-page replacement for prompt() (browsers can suppress prompt()).
function inputModal(title, placeholder, type) {
  return new Promise((resolve) => {
    const overlay = document.createElement("div");
    overlay.className = "modal-overlay";
    overlay.innerHTML = `<div class="modal"><h3>${title}</h3>` +
      `<input id="im_input" type="${type || "text"}" placeholder="${placeholder || ""}">` +
      `<div class="modal-actions"><button class="primary" id="im_ok">確定</button>` +
      `<button class="link" id="im_cancel">取消</button></div></div>`;
    document.body.appendChild(overlay);
    const inp = overlay.querySelector("#im_input");
    setTimeout(() => inp.focus(), 0);
    const done = (v) => { document.body.removeChild(overlay); resolve(v); };
    overlay.querySelector("#im_ok").onclick = () => done(inp.value);
    overlay.querySelector("#im_cancel").onclick = () => done(null);
    overlay.onclick = (e) => { if (e.target === overlay) done(null); };
    inp.addEventListener("keydown", (e) => { if (e.key === "Enter") done(inp.value); });
  });
}

// The auto-attribution dialog ALWAYS appears on 投幣/儲值.
// Returns a real_txn id (number) to attribute, "manual" to record normally,
// or "cancel" to abort the action entirely.
async function autoAttributeFlow(kind, points, isSelf) {
  if (!isSelf) {
    return await infoDialog("此操作為代他人記錄，不進行自動歸戶。要記為一般紀錄嗎？");
  }
  if (!window.AUTO_ATTRIBUTE) {
    return await infoDialog("未開啟自動歸戶。要將這筆記為一般紀錄嗎？");
  }
  const res = await NK.post("/api/attribution/match", { kind, points });
  if (!res) return "cancel";  // request failed (already alerted)
  if (!res.candidates.length) {
    return await infoDialog("未匹配：找不到金額相同且尚未歸戶的真實紀錄。要記為一般紀錄嗎？");
  }
  return await pickCandidate(res.candidates);
}

// Confirmation dialog for the off / no-match / on-behalf cases.
function infoDialog(message) {
  return new Promise((resolve) => {
    const overlay = document.createElement("div");
    overlay.className = "modal-overlay";
    overlay.innerHTML = `<div class="modal"><h3>自動歸戶</h3><p>${message}</p>` +
      `<div class="modal-actions">` +
      `<button class="primary" data-act="manual">記為一般紀錄</button>` +
      `<button class="link" data-act="cancel">取消</button></div></div>`;
    document.body.appendChild(overlay);
    const done = (v) => { document.body.removeChild(overlay); resolve(v); };
    overlay.querySelector('[data-act="manual"]').onclick = () => done("manual");
    overlay.querySelector('[data-act="cancel"]').onclick = () => done("cancel");
    overlay.onclick = (e) => { if (e.target === overlay) done("cancel"); };
  });
}

// occurred_at arrives as naive UTC ("2026-06-15T02:30:00"). Parse it AS UTC and
// render in Taipei time — the previous code sliced the raw string, showing the
// UTC time (8h off). The rest of the app formats times server-side via localdt.
function fmtTxnTime(s) {
  if (!s) return "";
  const d = new Date(s.replace(" ", "T") + "Z");
  if (isNaN(d.getTime())) return s.replace("T", " ").slice(5, 16);
  return d.toLocaleString("zh-TW", {
    timeZone: "Asia/Taipei", month: "2-digit", day: "2-digit",
    hour: "2-digit", minute: "2-digit", hour12: false,
  });
}

function pickCandidate(candidates) {
  return new Promise((resolve) => {
    const overlay = document.createElement("div");
    overlay.className = "modal-overlay";
    const items = candidates.map(c => {
      const t = fmtTxnTime(c.occurred_at);
      return `<button class="cand" data-id="${c.id}">${t} · ${c.raw_name} · ${c.value} 點</button>`;
    }).join("");
    overlay.innerHTML =
      `<div class="modal"><h3>找到金額相同且未歸戶的真實紀錄，哪一筆是你的？</h3>` +
      `<div class="cand-list">${items}</div><div class="modal-actions">` +
      `<button class="link" data-act="manual">都不是 / 不歸戶（記為一般紀錄）</button>` +
      `<button class="link" data-act="cancel">取消</button></div></div>`;
    document.body.appendChild(overlay);
    const done = (v) => { document.body.removeChild(overlay); resolve(v); };
    overlay.querySelectorAll(".cand").forEach(b => (b.onclick = () => done(+b.dataset.id)));
    overlay.querySelector('[data-act="manual"]').onclick = () => done("manual");
    overlay.querySelector('[data-act="cancel"]').onclick = () => done("cancel");
    overlay.onclick = (e) => { if (e.target === overlay) done("cancel"); };
  });
}

// points credited for a NT$ amount: floor(money/rate) + VIP bonus (>= threshold)
function topupPoints(money) {
  const rate = window.RATE || 10;
  const base = Math.floor(money / rate);
  const pct = window.VIP_BONUS_PCT || 0;
  const bonus = money >= (window.BONUS_MIN_TOPUP || 300) ? Math.floor(base * pct / 100) : 0;
  return { base, bonus, total: base + bonus };
}

function updateTopupPreview() {
  const el = document.getElementById("tu_preview");
  if (!el) return;
  const b = topupPoints(+val("tu_money") || 0);
  let s = `基礎 ${b.base} 點`;
  if (b.bonus) s += ` ＋ VIP 加贈 ${b.bonus} 點`;
  el.textContent = s + ` = 共 ${b.total} 點`;
}

async function doTopup() {
  const member_id = +val("tu_member"), money = +val("tu_money");
  if (!money || money <= 0) { alert("請輸入金額"); return; }
  const total = topupPoints(money).total;
  if (total <= 0) { alert("金額太少，不足 1 點"); return; }
  // top-up by money; points (incl. VIP bonus) are computed server-side
  const chosen = await autoAttributeFlow("topup", total, member_id === window.MEMBER_ID);
  if (chosen === "cancel") return;
  if (chosen !== "manual") {
    if (await NK.post(`/api/attribution/self/${chosen}`, { money_nt: money })) NK.reload();
    return;
  }
  if (await NK.post("/api/topups", { member_id, money_nt: money, ...timeVal("tu_time") })) NK.reload();
}

async function setRate() {
  const rate = val("rate_input");
  if (!rate || +rate <= 0) { alert("請輸入有效匯率"); return; }
  const r = await NK.post("/api/admin/rate", { rate });
  if (r) { alert("匯率已更新為 " + r.rate + " NT$/點"); NK.reload(); }
}

// Anti-addiction: warn (but allow override) when this play would push the
// member's own daily spend past their limit. Only for self-records.
function antiAddictionOk(member_id, points) {
  if (!window.ANTI_ADDICTION) return true;
  if (member_id !== window.MEMBER_ID) return true;
  const after = (window.TODAY_SPENT || 0) + points;
  if (after <= window.DAILY_LIMIT) return true;
  return confirm(
    `⚠️ 防沉迷提醒\n\n今日已消耗 ${window.TODAY_SPENT || 0} 點，加上這筆 ${points} 點將達 ${after} 點，` +
    `超過你設定的每日上限 ${window.DAILY_LIMIT} 點。\n\n確定仍要繼續投幣嗎？`
  );
}

async function doPlay() {
  const member_id = +val("pl_member"), points = +val("pl_points"), note = val("pl_note") || null;
  if (!points || points <= 0) { alert("請輸入點數"); return; }
  if (!antiAddictionOk(member_id, points)) return;
  const chosen = await autoAttributeFlow("pay", points, member_id === window.MEMBER_ID);
  if (chosen === "cancel") return;
  if (chosen !== "manual") {
    if (await NK.post(`/api/attribution/self/${chosen}`, {})) NK.reload();
    return;
  }
  if (await NK.post("/api/plays", { member_id, points, note, ...timeVal("pl_time") })) NK.reload();
}

async function saveAntiAddiction() {
  const enabled = document.getElementById("anti_toggle").checked;
  const daily_limit = +document.getElementById("anti_limit").value || 30;
  const r = await NK.post("/api/auth/anti-addiction", { enabled, daily_limit });
  if (r) {
    window.ANTI_ADDICTION = r.anti_addiction;
    window.DAILY_LIMIT = r.daily_spend_limit;
    document.getElementById("anti_limit").value = r.daily_spend_limit;
  } else {
    document.getElementById("anti_toggle").checked = !enabled;
  }
}

async function toggleAutoAttribute() {
  const enabled = document.getElementById("aa_toggle").checked;
  const r = await NK.post("/api/auth/auto-attribute", { enabled });
  if (r) { window.AUTO_ATTRIBUTE = enabled; }
  else { document.getElementById("aa_toggle").checked = !enabled; }
}
async function doTransfer() {
  const f = +val("tr_from"), t = +val("tr_to");
  if (f === t) { alert("不能轉給自己"); return; }
  const ok = await NK.post("/api/transfers",
    { from_member_id: f, to_member_id: t, points: +val("tr_points") });
  if (ok) NK.reload();
}

async function attribute(id) {
  const ok = await NK.post(`/api/admin/real-transactions/${id}/attribute`, { member_id: +val("attr_" + id) });
  if (ok) NK.reload();
}
async function ignoreTxn(id) {
  const reason = await inputModal("忽略原因", "原因"); if (!reason) return;
  if (await NK.post(`/api/admin/real-transactions/${id}/ignore`, { reason })) NK.reload();
}
async function syncNow() {
  const r = await NK.post("/api/admin/sync/run-now", {});
  if (r) { alert("同步狀態：" + r.status + "，新增 " + r.rows_inserted + " 筆"); NK.reload(); }
}

async function setSyncSince() {
  const since = val("sync_since");
  const r = await NK.post("/api/admin/sync-since", { since });
  if (r) { alert(since ? ("已設定同步起始日：" + r.since) : "已清除同步起始日"); NK.reload(); }
}
async function clearSyncSince() {
  if (await NK.post("/api/admin/sync-since", { since: null })) { alert("已清除同步起始日"); NK.reload(); }
}

async function resetDatabase() {
  if (!confirm("確定要重置整個資料庫嗎？\n會刪除所有成員(除了你)、所有紀錄與設定，無法復原！")) return;
  const pwd = await inputModal("請輸入你的密碼以確認重置", "密碼", "password");
  if (!pwd) return;
  if (await NK.post("/api/admin/reset", { password: pwd })) {
    alert("已重置資料庫"); location.href = "/dashboard";
  }
}

// ---- member management (admin) ----
async function addMember() {
  const username = val("nm_username"), name = val("nm_name"), pwd = val("nm_pwd"), role = val("nm_role");
  if (!username || !name || !pwd) { alert("請填使用者名稱、暱稱、密碼"); return; }
  if (await NK.post("/api/members", { username, display_name: name, password: pwd, role })) NK.reload();
}
async function changeRole(id) {
  const role = val("role_" + id);
  if (await NK.post(`/api/members/${id}/update`, { role })) NK.reload();
}
async function renameMember(id) {
  const name = await inputModal("改暱稱", "新的暱稱");
  if (!name) return;
  if (await NK.post(`/api/members/${id}/update`, { display_name: name })) NK.reload();
}
async function setActive(id, active) {
  if (!active && !confirm("確定停用此帳號？對方會立即被登出。")) return;
  if (await NK.post(`/api/members/${id}/status`, { is_active: active })) NK.reload();
}
async function resetPwd(id) {
  const pwd = await inputModal("重設密碼", "至少 6 碼");
  if (!pwd) return;
  if (pwd.length < 6) { alert("密碼至少 6 碼"); return; }
  if (await NK.post(`/api/members/${id}/reset-password`, { new_password: pwd })) {
    alert("已重設，對方需用新密碼重新登入"); NK.reload();
  }
}
// 補歸戶: link an existing manual record to a matching unattributed real txn
async function supplementAttribute(btn) {
  const id = +btn.dataset.id;
  const kind = btn.dataset.type === "TOPUP" ? "topup" : "pay";
  const points = +btn.dataset.points;
  const res = await NK.post("/api/attribution/match", { kind, points });
  if (!res) return;
  if (!res.candidates.length) { alert("找不到金額相同且尚未歸戶的真實紀錄"); return; }
  const chosen = await pickCandidate(res.candidates);
  if (chosen === "manual" || chosen === "cancel") return;
  const cand = res.candidates.find(c => c.id === chosen);
  const overwrite_note = confirm(
    `是否將備註覆蓋為「${cand ? cand.raw_name : ''}」？\n確定＝覆蓋；取消＝保留原本備註`);
  if (await NK.post(`/api/ledger/${id}/attribute`,
                    { real_txn_id: chosen, overwrite_note })) NK.reload();
}

async function deleteMember(id) {
  if (!confirm("確定刪除此帳號？")) return;
  let r = await fetch(`/api/members/${id}`, {
    method: "DELETE", headers: { "X-CSRF-Token": window.CSRF },
  });
  if (r.ok) { alert("已刪除"); NK.reload(); return; }
  let data = null; try { data = await r.json(); } catch (e) {}
  if (r.status === 409) {
    const msg = (data && data.detail) || "此帳號有關聯紀錄";
    if (!confirm(msg + "\n\n要『強制刪除』——連同所有紀錄一起刪除嗎？此動作無法復原！")) return;
    r = await fetch(`/api/members/${id}?force=true`, {
      method: "DELETE", headers: { "X-CSRF-Token": window.CSRF },
    });
    if (r.ok) { alert("已強制刪除"); NK.reload(); return; }
    try { data = await r.json(); } catch (e) {}
    alert((data && data.detail) || ("錯誤 " + r.status));
    return;
  }
  alert((data && data.detail) || ("錯誤 " + r.status));
}

// ---- edit / delete a ledger record ----
function delEntry(id) {
  if (!confirm("確定刪除這筆紀錄？\n（轉點會一併刪除另一方；已歸戶的會釋放回真實紀錄）")) return;
  NK.del(`/api/ledger/${id}`).then(r => { if (r) NK.reload(); });
}

function startEdit(btn) {
  const id = btn.dataset.id, type = btn.dataset.type;
  const locked = btn.dataset.locked === "1";
  const isTopup = type === "TOPUP";
  const overlay = document.createElement("div");
  overlay.className = "modal-overlay";
  let fields = `<label>點數（${type}）<input id="ed_points" type="number" ${locked ? "disabled" : ""}></label>`;
  if (isTopup) fields += `<label>金額 NT$<input id="ed_money" type="number" step="0.01" ${locked ? "disabled" : ""}></label>`;
  fields += `<label>備註<input id="ed_note" type="text"></label>`;
  const lockNote = locked
    ? `<p class="muted">此筆已歸戶到真實紀錄，金額不可改（可改備註，或刪除後重新歸戶）。</p>` : "";
  overlay.innerHTML = `<div class="modal"><h3>編輯紀錄</h3>${fields}${lockNote}` +
    `<div class="modal-actions"><button class="primary" id="ed_save">儲存</button>` +
    `<button class="link" id="ed_cancel">取消</button></div></div>`;
  document.body.appendChild(overlay);
  // set values via properties (avoids HTML-injection in attribute values)
  overlay.querySelector("#ed_points").value = btn.dataset.points || "";
  if (isTopup) overlay.querySelector("#ed_money").value = btn.dataset.money || "";
  overlay.querySelector("#ed_note").value = btn.dataset.note || "";
  const close = () => document.body.removeChild(overlay);
  overlay.querySelector("#ed_cancel").onclick = close;
  overlay.onclick = (e) => { if (e.target === overlay) close(); };
  overlay.querySelector("#ed_save").onclick = async () => {
    const body = { note: overlay.querySelector("#ed_note").value };
    if (!locked) {
      body.points = +overlay.querySelector("#ed_points").value;
      if (isTopup) body.money_nt = overlay.querySelector("#ed_money").value;
    }
    if (await NK.post(`/api/ledger/${id}/edit`, body)) { close(); NK.reload(); }
  };
}

// initialize the top-up points preview if the field is present
if (document.getElementById("tu_money")) updateTopupPreview();

// default the optional 指定時間 fields to the current local time
["pl_time", "tu_time"].forEach((id) => {
  const el = document.getElementById(id);
  if (el && !el.value) el.value = nowLocalValue();
});
