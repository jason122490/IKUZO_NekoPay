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
  reload() { location.reload(); },
};

function val(id) { return document.getElementById(id).value; }

async function doTopup() {
  const ok = await NK.post("/api/topups", {
    member_id: +val("tu_member"), points: +val("tu_points"), money_nt: val("tu_money"),
  });
  if (ok) NK.reload();
}
async function doPlay() {
  const ok = await NK.post("/api/plays", {
    member_id: +val("pl_member"), points: +val("pl_points"), note: val("pl_note") || null,
  });
  if (ok) NK.reload();
}
async function doTransfer() {
  const f = +val("tr_from"), t = +val("tr_to");
  if (f === t) { alert("不能轉給自己"); return; }
  const ok = await NK.post("/api/transfers", { from_member_id: f, to_member_id: t, points: +val("tr_points") });
  if (ok) NK.reload();
}

async function attribute(id) {
  const ok = await NK.post(`/api/admin/real-transactions/${id}/attribute`, { member_id: +val("attr_" + id) });
  if (ok) NK.reload();
}
async function ignoreTxn(id) {
  const reason = prompt("忽略原因？"); if (!reason) return;
  const ok = await NK.post(`/api/admin/real-transactions/${id}/ignore`, { reason });
  if (ok) NK.reload();
}
async function approveClaim(id) { if (await NK.post(`/api/admin/claims/${id}/approve`, {})) NK.reload(); }
async function rejectClaim(id) { if (await NK.post(`/api/admin/claims/${id}/reject`, {})) NK.reload(); }
async function syncNow() {
  const r = await NK.post("/api/admin/sync/run-now", {});
  if (r) { alert("同步狀態：" + r.status + "，新增 " + r.rows_inserted + " 筆"); NK.reload(); }
}
