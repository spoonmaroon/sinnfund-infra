// Signals page: every pending LIVE signal, read from the signal API with the visitor's own sender key.
// The /live/ site has no login, so the key is the access control; it stays in this tab (sessionStorage).
// Uses app.js globals ($, esc, num, saveText).
const SG_API = "/prediction/signals";

function sgSavedKey() {
  try { return sessionStorage.getItem("sgKey") || ""; } catch { return ""; }
}

function sgMessage(text, bad = false) {
  $("sgMsg").textContent = text;
  $("sgMsg").className = bad ? "sg-msg neg" : "sg-msg";
}

async function sgFetch(path) {
  const response = await fetch(`${SG_API}/${path}`, { headers: { Authorization: `Bearer ${$("sgKey").value.trim()}` } });
  if (response.status === 401) throw new Error("That key was not accepted. Use the sender key you send signals with.");
  if (!response.ok) throw new Error((await response.json().catch(() => ({}))).detail || `Signal API error ${response.status}`);
  return response;
}

function renderSignals(list) {
  const byAnalyst = new Map();
  for (const s of list) byAnalyst.set(s.analyst, (byAnalyst.get(s.analyst) || 0) + 1);
  $("sgNote").textContent = `${list.length} pending from ${byAnalyst.size} sender${byAnalyst.size === 1 ? "" : "s"}`;
  $("sgSummary").textContent = [...byAnalyst].sort((a, b) => b[1] - a[1]).map(([who, n]) => `${who} ${n}`).join(" · ") || "No pending LIVE signals.";
  $("sgRows").innerHTML = [...list].sort((a, b) => b.sent_at.localeCompare(a.sent_at)).map((s) => `<tr><td>${esc(new Date(s.sent_at).toLocaleString())}</td><td>${esc(s.analyst)}</td><td>${esc(s.signal_id)}</td><td><strong>${esc(s.ticker)}</strong></td><td class="num">${num.format(s.target_price)}</td></tr>`).join("") || '<tr><td colspan="5">No pending LIVE signals.</td></tr>';
}

async function sgLoad() {
  sgMessage("Loading…");
  try {
    renderSignals(await (await sgFetch("pending")).json());
    try { sessionStorage.setItem("sgKey", $("sgKey").value.trim()); } catch {}
    $("sgDownload").disabled = false;
    sgMessage("");
  } catch (error) {
    $("sgDownload").disabled = true;
    sgMessage(error.message, true);
  }
}

async function sgDownload() {
  sgMessage("Pricing signals with LSEG…");
  try {
    const response = await sgFetch("portfolio.csv"), text = await response.text();
    const left = (response.headers.get("X-Signals-Left-Out") || "").split(",").filter(Boolean);
    saveText(`signals-targets-${new Date().toISOString().slice(0, 10)}.csv`, text);
    sgMessage(`Downloaded ${text.trim().split("\n").length - 1} rows.` + (left.length ? ` Left out (no LSEG price, or target equals price): ${left.join(", ")}` : ""));
  } catch (error) {
    sgMessage(error.message, true);
  }
}

$("sgForm").addEventListener("submit", (e) => { e.preventDefault(); sgLoad(); });
$("sgDownload").addEventListener("click", sgDownload);
$("sgKey").value = sgSavedKey();
if ($("sgKey").value) sgLoad();
