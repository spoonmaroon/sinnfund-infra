// Signals page: every pending LIVE signal, from the signal API's public read-only routes.
// Uses app.js globals ($, esc, num, saveText).
const SG_API = "/prediction/signals";
let sgLoaded = false;

function sgMessage(text, bad = false) {
  $("sgMsg").textContent = text;
  $("sgMsg").className = bad ? "sg-msg neg" : "sg-msg";
}

async function sgFetch(path) {
  const response = await fetch(`${SG_API}/${path}`);
  if (!response.ok) throw new Error((await response.json().catch(() => ({}))).detail || `Signal API error ${response.status}`);
  return response;
}

const sgWhen = (iso) => new Date(iso).toLocaleString([], { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });

function renderSignals(list) {
  const byAnalyst = new Map();
  for (const s of list) byAnalyst.set(s.analyst, (byAnalyst.get(s.analyst) || 0) + 1);
  const newest = [...list].sort((a, b) => b.sent_at.localeCompare(a.sent_at));
  $("sgCount").textContent = num.format(list.length);
  $("sgSenders").textContent = num.format(byAnalyst.size);
  $("sgLatest").textContent = newest.length ? sgWhen(newest[0].sent_at) : "—";
  $("sgLatestNote").textContent = newest.length ? `${newest[0].analyst} · ${newest[0].ticker}` : "No pending signals";
  $("sgNote").textContent = `${list.length} pending signal${list.length === 1 ? "" : "s"}`;
  $("sgUpdated").textContent = `Updated ${new Date().toLocaleTimeString([], { hour: "numeric", minute: "2-digit" })}`;
  $("sgChips").innerHTML = [...byAnalyst].sort((a, b) => b[1] - a[1]).map(([who, n]) => `<span class="sg-chip">${esc(who)}<b>${n}</b></span>`).join("");
  $("sgRows").innerHTML = newest.map((s) => `<tr><td>${esc(sgWhen(s.sent_at))}</td><td>${esc(s.analyst)}</td><td><strong>${esc(s.ticker)}</strong></td><td class="num">${num.format(s.target_price)}</td><td>${esc(s.signal_id)}</td></tr>`).join("") || '<tr><td colspan="5">No pending LIVE signals.</td></tr>';
  $("sgDownload").disabled = !list.length;
}

async function sgLoad() {
  try {
    renderSignals(await (await sgFetch("pending")).json());
    sgLoaded = true;
  } catch (error) {
    $("sgNote").textContent = "Signals unavailable";
    $("sgRows").innerHTML = `<tr><td colspan="5">Could not load signals: ${esc(error.message)}</td></tr>`;
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

$("sgDownload").addEventListener("click", sgDownload);
if (window.location?.hash === "#signals") showPage("signals"); // shared links open the tab
