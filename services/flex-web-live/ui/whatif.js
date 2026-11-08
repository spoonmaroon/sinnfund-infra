// What if page: a basket of proposed trades -> risk before and after.
// Uses app.js globals ($, esc, num, money, price, signed, dateLabel, ACCOUNT_NAMES, namesLoaded).
const WI_ROWS = 3;

function wiRow(trade = {}) {
  const row = document.createElement("div");
  row.className = "wi-row";
  row.innerHTML = `<span class="wi-num"></span>
    <input aria-label="Symbol" placeholder="e.g. NVDA" maxlength="40" autocomplete="off" value="${esc(trade.symbol || "")}">
    <input aria-label="Quantity" placeholder="100 or -50" inputmode="decimal" value="${esc(trade.quantity || "")}">
    <input aria-label="Account" placeholder="All accounts" list="wiAccounts" value="${esc(trade.account || "")}">
    <button class="wi-remove" type="button" aria-label="Remove row">×</button>`;
  return row;
}

function wiRenumber() {
  const rows = [...$("wiRows").children];
  rows.forEach((row, i) => { row.firstElementChild.textContent = i + 1; });
  const filled = wiTrades().length;
  $("wiCount").textContent = filled ? `${filled} trade${filled === 1 ? "" : "s"}` : "";
}

function wiSetRows(trades) {
  $("wiRows").innerHTML = "";
  const list = trades.length ? trades : Array.from({ length: WI_ROWS }, () => ({}));
  list.forEach((trade) => $("wiRows").appendChild(wiRow(trade)));
  wiRenumber();
}

function wiTrades() {
  return [...$("wiRows").children].map((row) => {
    const [symbol, quantity, account] = [...row.querySelectorAll("input")].map((input) => input.value.trim());
    return { symbol, quantity, account };
  }).filter((trade) => trade.symbol || trade.quantity);
}

// Account column accepts the number or the fund sheet's name, in any case.
function wiAccountId(text) {
  if (!text) return "";
  const wanted = text.toLowerCase();
  const match = Object.entries(ACCOUNT_NAMES).find(([id, name]) => id.toLowerCase() === wanted || name.toLowerCase() === wanted);
  return match ? match[0] : text;
}

// Spreadsheet paste (tab-separated) or CSV/semicolon files; quoted fields like "1,000" are kept whole.
function wiParse(text) {
  const lines = text.replace(/\r/g, "").split("\n").filter((line) => line.trim());
  if (!lines.length) return [];
  const first = lines[0];
  const sep = first.includes("\t") ? "\t" : (first.split(";").length > first.split(",").length ? ";" : ",");
  const split = (line) => sep === "\t" ? line.split("\t").map((cell) => cell.replace(/^"(.*)"$/, "$1"))
    : [...line.matchAll(new RegExp(`(?:"([^"]*(?:""[^"]*)*)"|([^${sep}]*))(?:${sep}|$)`, "g"))]
      .slice(0, -1).map((m) => (m[1] !== undefined ? m[1].replace(/""/g, '"') : m[2]));
  let cells = lines.map(split).map((row) => row.map((cell) => (cell || "").trim()));
  let columns = { symbol: 0, quantity: 1, account: 2 };
  const header = cells[0].map((cell) => cell.toLowerCase());
  if (header.some((cell) => /symbol|ticker/.test(cell))) {
    const find = (pattern, fallback) => { const i = header.findIndex((cell) => pattern.test(cell)); return i < 0 ? fallback : i; };
    columns = { symbol: find(/symbol|ticker/, 0), quantity: find(/qty|quantity|shares|position|amount/, 1), account: find(/account|acct|book/, -1) };
    cells = cells.slice(1);
  }
  return cells.map((row) => ({
    symbol: row[columns.symbol] || "",
    quantity: (row[columns.quantity] || "").replace(/[$,\s]/g, "").replace(/^\((.*)\)$/, "-$1"),
    account: columns.account >= 0 ? row[columns.account] || "" : "",
  })).filter((trade) => trade.symbol);
}

function wiLoad(text) {
  const trades = wiParse(text);
  if (!trades.length) return false;
  wiSetRows([...wiTrades(), ...trades]);
  return true;
}

function wiCard(label, before, after, money_ = true) {
  const change = after - before;
  const fmt = (v) => (money_ ? money.format(v) : num.format(v));
  const pct = before ? ` (${change > 0 ? "+" : ""}${num.format(change / Math.abs(before) * 100)}%)` : "";
  return `<article class="stat"><label>${label}</label><strong>${fmt(after)}</strong>
    <small>was ${fmt(before)} · <b class="wi-change">${change > 0 ? "+" : ""}${num.format(Math.round(change))}${pct}</b></small></article>`;
}

function wiRender(d) {
  const f = d.fund, b = d.basket;
  const name = (id) => (id ? ACCOUNT_NAMES[id] || id : "All accounts");
  const rejected = d.rejected.length ? `<section class="wi-warning"><strong>${d.rejected.length} row${d.rejected.length === 1 ? "" : "s"} left out</strong>
    ${d.rejected.map((r) => `<span>Row ${r.row} ${esc(r.symbol || "(blank)")}: ${esc(r.reason)}</span>`).join("")}</section>` : "";
  const delta = (before, after) => { const c = Math.round(after - before); return `${c > 0 ? "+" : ""}${num.format(c)}`; };
  const trades = d.trades.map((t) => `<tr><td>${t.row}</td><td><strong>${esc(t.symbol)}</strong></td><td>${esc(name(t.account))}</td>
    <td class="num">${num.format(t.quantity)}</td><td class="num">${t.fx_to_usd !== 1 ? num.format(t.price) + " local" : price.format(t.price)}</td>
    <td class="num">${signed(t.value)}</td><td class="num">${num.format(Math.round(t.stand_alone_var_95))}</td>
    <td class="num">${signed(t.es_contribution)}</td><td>${esc(t.sector)}</td></tr>`).join("");
  const accounts = d.accounts.map((a) => `<tr><td>${esc(name(a.id))}</td><td class="num">${a.trades}</td>
    <td class="num">${num.format(Math.round(a.before.var_95))}</td><td class="num">${num.format(Math.round(a.after.var_95))}</td><td class="num">${delta(a.before.var_95, a.after.var_95)}</td>
    <td class="num">${num.format(Math.round(a.before.es_97_5))}</td><td class="num">${num.format(Math.round(a.after.es_97_5))}</td><td class="num">${delta(a.before.es_97_5, a.after.es_97_5)}</td></tr>`).join("");
  const sectors = d.sectors.map((s) => `<tr><td>${esc(s.name)}</td><td class="num">${money.format(s.before_usd)} · ${num.format(s.before_pct)}%</td>
    <td class="num">${money.format(s.after_usd)} · ${num.format(s.after_pct)}%</td>
    <td class="num">${s.after_pct > s.before_pct ? "+" : ""}${num.format(Math.round((s.after_pct - s.before_pct) * 100) / 100)} pts</td></tr>`).join("");
  const worst = d.worst_days.map((w) => `<tr><td>${esc(dateLabel(w.date))}</td><td class="num">${signed(w.before)}</td><td class="num">${signed(w.trades)}</td><td class="num">${signed(w.after)}</td></tr>`).join("");
  $("wiAsOf").textContent = `Holdings ${dateLabel(d.as_of)} · ${d.scenarios} historical days`;
  $("wiResult").innerHTML = `${rejected}
    <section class="stats">${wiCard("Fund VaR 95%", f.before.var_95, f.after.var_95)}${wiCard("Fund VaR 99%", f.before.var_99, f.after.var_99)}
      ${wiCard("Expected shortfall 97.5%", f.before.es_97_5, f.after.es_97_5)}${wiCard("If the S&amp;P 500 falls 1%", f.before.sp500_down_1pct, f.after.sp500_down_1pct)}</section>
    <section class="card accounts"><div class="card-head"><div><p class="kicker">Trades</p><h2>${d.trades.length} trade${d.trades.length === 1 ? "" : "s"} modelled</h2>
      <p class="sub">Net ${money.format(b.net_value)} · gross ${money.format(b.gross_value)} · the basket alone: VaR 95% ${money.format(b.stand_alone_var_95)} · share of the fund's new expected shortfall ${money.format(b.es_contribution)}</p></div></div>
      <div class="account-list"><table class="acct-table"><thead><tr><th>#</th><th>Symbol</th><th>Account</th><th class="num">Quantity</th><th class="num">Price</th><th class="num">Value</th><th class="num">Own VaR 95%</th><th class="num">Share of ES</th><th>Sector</th></tr></thead><tbody>${trades}</tbody></table></div></section>
    ${accounts ? `<section class="card accounts"><div class="card-head"><div><p class="kicker">Accounts</p><h2>Accounts affected</h2></div></div><div class="account-list"><table class="acct-table"><thead><tr><th>Account</th><th class="num">Trades</th><th class="num">VaR 95% before</th><th class="num">After</th><th class="num">Change</th><th class="num">ES before</th><th class="num">After</th><th class="num">Change</th></tr></thead><tbody>${accounts}</tbody></table></div></section>` : ""}
    <section class="grid"><article class="card"><div class="card-head"><div><p class="kicker">Concentration</p><h2>Sectors traded</h2><p class="return-pending">Gross exposure and share of the fund (${money.format(d.gross_before)} → ${money.format(d.gross_after)} gross). Other sectors keep their dollars; their share shrinks as the book grows.</p></div></div><div class="account-list"><table class="acct-table compact"><thead><tr><th>Sector</th><th class="num">Before</th><th class="num">After</th><th class="num">Change</th></tr></thead><tbody>${sectors}</tbody></table></div></article>
    <article class="card"><div class="card-head"><div><p class="kicker">Stress</p><h2>Fund's worst days</h2><p class="return-pending">Today's book, the trades, and both together</p></div></div><div class="account-list"><table class="acct-table compact"><thead><tr><th>Day</th><th class="num">Book</th><th class="num">Trades</th><th class="num">Book with trades</th></tr></thead><tbody>${worst}</tbody></table></div></article></section>`;
}

// A row needs both a symbol and a numeric, non-zero quantity; blank account means all accounts.
function wiProblems() {
  const problems = [];
  [...$("wiRows").children].forEach((row, i) => {
    const [symbol, quantity] = [...row.querySelectorAll("input")];
    const s = symbol.value.trim(), q = quantity.value.trim().replace(/[$,\s]/g, "").replace(/^\((.*)\)$/, "-$1");
    symbol.classList.toggle("wi-invalid", !s && !!q);
    const badQty = !!s && (!q || !Number(q));
    quantity.classList.toggle("wi-invalid", badQty);
    if (!s && q) problems.push(`Row ${i + 1}: enter a symbol`);
    if (badQty) problems.push(`Row ${i + 1}: enter a quantity${q ? " (a non-zero number)" : ""}`);
  });
  return problems;
}

function wiRun() {
  const problems = wiProblems();
  if (problems.length) {
    $("wiResult").innerHTML = `<section class="wi-warning"><strong>Fix ${problems.length === 1 ? "this row" : "these rows"} first</strong>${problems.map((p) => `<span>${esc(p)}</span>`).join("")}</section>`;
    return Promise.resolve();
  }
  const trades = wiTrades().map((t) => ({ ...t, quantity: t.quantity.replace(/[$,\s]/g, ""), account: wiAccountId(t.account) }));
  if (!trades.length) {
    $("wiResult").innerHTML = `<section class="wi-warning">Add at least one trade: a symbol and a quantity.</section>`;
    return Promise.resolve();
  }
  $("wiRun").disabled = true;
  return fetch("api/v1/risk/whatif", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ trades }) })
    .then((r) => r.json().then((d) => {
      if (r.ok) wiRender(d);
      else $("wiResult").innerHTML = `<section class="wi-warning">${esc(typeof d.detail === "string" ? d.detail : "Check each row has a symbol and a quantity.")}</section>`;
    }))
    .catch(() => { $("wiResult").innerHTML = `<section class="wi-warning">What if is unavailable right now.</section>`; })
    .finally(() => { $("wiRun").disabled = false; });
}

wiSetRows([]);
// Every account with a statement, by fund-sheet name where there is one, otherwise by number.
Promise.all([fetch("api/v1/portfolio").then((r) => r.json()), namesLoaded]).then(([p]) => {
  const labels = (p.accounts || []).map((a) => ACCOUNT_NAMES[a.id] || a.id).sort((x, y) => x.localeCompare(y));
  $("wiAccounts").innerHTML = labels.map((label) => `<option value="${esc(label)}"></option>`).join("");
}).catch(() => {});
$("wiRun").addEventListener("click", wiRun);
$("wiAdd").addEventListener("click", () => { $("wiRows").appendChild(wiRow()); wiRenumber(); });
$("wiClear").addEventListener("click", () => { wiSetRows([]); $("wiResult").innerHTML = ""; });
$("wiRows").addEventListener("click", (e) => { if (e.target.closest(".wi-remove")) { e.target.closest(".wi-row").remove(); wiRenumber(); } });
$("wiRows").addEventListener("input", (e) => { e.target.classList.remove("wi-invalid"); wiRenumber(); });
$("wiRows").addEventListener("keydown", (e) => { if (e.key === "Enter") wiRun(); });
$("wiTemplate").addEventListener("click", () => {
  const link = document.createElement("a");
  link.href = URL.createObjectURL(new Blob(["symbol,quantity,account\nNVDA,100,\nGLD,-50,Metals & Materials\n"], { type: "text/csv" }));
  link.download = "what-if-trades.csv";
  link.click();
  URL.revokeObjectURL(link.href);
});
$("wiFile").addEventListener("change", (e) => { const file = e.target.files[0]; if (file) file.text().then(wiLoad); e.target.value = ""; });
$("wiDrop").addEventListener("dragover", (e) => { e.preventDefault(); $("wiDrop").classList.add("wi-dropping"); });
$("wiDrop").addEventListener("dragleave", () => $("wiDrop").classList.remove("wi-dropping"));
$("wiDrop").addEventListener("drop", (e) => {
  e.preventDefault();
  $("wiDrop").classList.remove("wi-dropping");
  const file = e.dataTransfer.files[0];
  if (file) file.text().then(wiLoad);
});
// Paste copied spreadsheet rows anywhere on the page; a single value pastes into the field as usual.
document.addEventListener("paste", (e) => {
  if ($("whatifPage").hidden) return;
  const text = e.clipboardData?.getData("text") || "";
  if (/[\t\n]/.test(text.trim()) && wiLoad(text)) e.preventDefault();
});
