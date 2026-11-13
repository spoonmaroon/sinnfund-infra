// Runs ui/app.js against fake API data and checks what the dashboard renders.
// Run: node --test services/flex-web-live/tests/
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

const APP = readFileSync(new URL("../ui/app.js", import.meta.url), "utf8")
  + "\n" + readFileSync(new URL("../ui/whatif.js", import.meta.url), "utf8")
  + "\n" + readFileSync(new URL("../ui/heatmap.js", import.meta.url), "utf8")
  + "\nreturn { positionsCsv, csvCell, wiParse, wiRender, wiAccountId, wiProblems, wiOpen, wiMerge, hmSquarify, hmSectors, hmColor, renderHeatmap };";

// Minimal stand-in for DOM elements: records text/HTML, ignores everything else.
function fakeElement() {
  const el = {
    textContent: "", innerHTML: "", className: "", hidden: false, value: "",
    dataset: {}, style: {}, options: [], children: [],
    classList: { add() {}, remove() {}, toggle() {}, contains: () => false },
    handlers: {},
    addEventListener(type, fn) { this.handlers[type] = fn; },
  };
  return new Proxy(el, { get: (t, k) => (k in t ? t[k] : () => fakeElement()) });
}

async function render(report, sectors = { sectors: {} }, history = { points: [] }, risk = { available: false, reason: "not run" }, extra = {}) {
  const elements = new Map();
  const byId = (id) => elements.get(id) ?? elements.set(id, fakeElement()).get(id);
  const responses = {
    "api/v1/portfolio": { totals: {}, allocation: [] },
    "api/v1/report": report,
    "api/v1/sectors": sectors,
    "api/v1/prices/validation": { coverage_pct: 100, value_coverage_pct: 100, updated_positions: 1, positions: 1 },
    "api/v1/history": history,
    "api/v1/risk": risk,
    ...extra,
    "static/account-names.json": { U9000001: "Lifeboat", U9000002: "Tier One Acct", U9000004: "Factor Investing - Short Interest", U9000005: "Quant" },
  };
  const context = {
    document: {
      getElementById: byId, body: fakeElement(), addEventListener() {}, createElement: () => fakeElement(),
      querySelector: (selector) => byId(`query:${selector}`), querySelectorAll: () => [],
    },
    window: { scrollTo() {} },
    ResizeObserver: class { observe() {} },
    fetch: async (url) => ({ ok: url in responses, json: async () => responses[url] }),
    console,
  };
  byId.api = new Function(...Object.keys(context), APP)(...Object.values(context));
  await new Promise((resolve) => setTimeout(resolve, 0));
  return byId;
}

const position = (fields) => ({
  account_id: "U1", conid: "1", symbol: "AAA", description: "A", asset_category: "STK",
  currency: "USD", fx_rate_to_base: "1", side: "Long", quantity: "10", multiplier: "1",
  mark_price: "110", market_value: "1100", cost_basis_money: "1000", unrealized_pnl: "100",
  close_mark_price: "110", delayed_mark_price: "110", day_change_price: "10",
  day_change_pct: "10", day_change_value: "100", ...fields,
});

test("headline totals and day change come from the data", async () => {
  const $ = await render({ date: "20261106", positions: [position({})], lots: [] });
  assert.equal($("value").textContent, "$1,100");
  assert.equal($("pnlValue").textContent, "$100");
  assert.equal($("delta").textContent, "+$100 on the latest session");
  assert.equal($("positions").textContent, "1");
});

test("foreign positions are summed in USD and priced in their own currency", async () => {
  const yen = position({
    account_id: "U2", conid: "2", symbol: "8136.T", currency: "JPY", fx_rate_to_base: "0.01",
    mark_price: "1216", close_mark_price: "1216", delayed_mark_price: "1216", market_value: "608000", cost_basis_money: "700000",
    unrealized_pnl: "-92000", day_change_price: "16", day_change_value: "80",
  });
  const $ = await render({ date: "20261106", positions: [position({}), yen], lots: [] });
  assert.equal($("value").textContent, "$7,180"); // 1,100 + 608,000 × 0.01
  assert.match($("rows").innerHTML, /¥1,216/);
});

test("missing previous close shows a dash instead of a zero change", async () => {
  const fresh = position({ day_change_price: "", day_change_pct: "", day_change_value: "" });
  const $ = await render({ date: "20261106", positions: [fresh], lots: [] });
  assert.match($("rows").innerHTML, /muted-cell">—/);
  assert.equal($("delta").textContent, "+$0 on the latest session");
});

test("close-only rows use the small close badge, not the dialog close button style", async () => {
  const $ = await render({ date: "20261106", positions: [position({ delayed_mark_price: "" })], lots: [] });
  assert.match($("rows").innerHTML, /source compact source-close">Close/);
  assert.doesNotMatch($("rows").innerHTML, /class="source compact close"/);
});

test("accounts table lists names, allocation and negatives", async () => {
  const loser = position({ account_id: "U9000001", conid: "3", unrealized_pnl: "-50", day_change_value: "-5" });
  const $ = await render({ date: "20261106", positions: [position({}), loser], lots: [] });
  const html = $("accountList").innerHTML;
  assert.match(html, /<td>U9000001<\/td><td>Lifeboat<\/td>/);
  assert.match(html, /50%/);
  assert.match(html, /class="neg">-50</);
});

test("history chart draws the cumulative P&L from daily statements", async () => {
  const history = { points: [
    { date: "20261104", cumulative_pnl: 0, day_pnl: null },
    { date: "20261105", cumulative_pnl: 1500, day_pnl: 1500 },
    { date: "20261106", cumulative_pnl: -250, day_pnl: -1750 },
  ] };
  const $ = await render({ date: "20261106", positions: [position({})], lots: [] }, undefined, history);
  assert.match($("historyChart").innerHTML, /<path class="line"/);
  assert.match($("historyChart").innerHTML, /Nov 6, 2026: -\$250 cumulative, -\$1,750 on the day/);
  assert.match($("historyTotal").innerHTML, /-\$250/);
  assert.match($("historyNote").textContent, /3 daily statements/);
});

test("history with a single statement shows a message instead of a chart", async () => {
  const $ = await render({ date: "20261106", positions: [position({})], lots: [] }, undefined,
    { points: [{ date: "20261106", cumulative_pnl: 0, day_pnl: null }] });
  assert.match($("historyChart").innerHTML, /Not enough daily statements/);
});

test("sectors show day change per sector and per holding", async () => {
  const $ = await render({ date: "20261106", positions: [position({ symbol: "AAA" })], lots: [] },
    { sectors: { AAA: "Energy" } });
  const html = $("sectorList").innerHTML;
  assert.match(html, /<strong>Energy<\/strong>/);
  assert.match(html, /\$100<\/span><\/strong><span>day chg/);
});

test("accounts IBKR left out of the latest file are called out", async () => {
  const $ = await render({ date: "20261106", positions: [position({})], lots: [],
    carried_forward: { from: "20261105", accounts: ["U9000002", "U9000003"] } });
  assert.equal($("coverage").textContent, "2 accounts missing from IBKR's latest file, shown from Nov 5, 2026");
});

test("net liquidation value adds cash, including cash-only accounts", async () => {
  const short = position({ account_id: "U9000004", conid: "4", market_value: "-950", unrealized_pnl: "0" });
  const $ = await render({ date: "20261106", positions: [position({}), short], lots: [],
    cash: { U1: "0", U9000004: "3950", U9000005: "1000", U9000006: "0" } });
  assert.equal($("value").textContent, "$5,100"); // 1,100 + -950 + 3,950 + 1,000
  assert.equal($("valueNote").textContent, "$150 positions · $4,950 cash");
  const html = $("accountList").innerHTML;
  assert.match(html, /<td>U9000004<\/td><td>Factor Investing - Short Interest<\/td><td class="num"><span class="">3,000<\/span><\/td><td class="num">59%/);
  assert.match(html, /<td>U9000005<\/td><td>Quant<\/td>/);
  assert.doesNotMatch(html, /U9000006/); // empty account: no cash, no positions
});

test("period buttons rebase the curve to zero at the start of the window", async () => {
  const history = { points: [
    { date: "20261009", cumulative_pnl: 0, day_pnl: null },
    { date: "20261029", cumulative_pnl: -200, day_pnl: -200 },
    { date: "20261030", cumulative_pnl: -500, day_pnl: -300 },
    { date: "20261106", cumulative_pnl: -400, day_pnl: 100 },
  ] };
  const $ = await render({ date: "20261106", positions: [position({})], lots: [] }, undefined, history);
  assert.match($("historyTotal").innerHTML, /-\$400/);
  $("periods").handlers.click({ target: { closest: () => ({ textContent: "1W" }) } });
  // 1W starts Oct 30, measured from the Oct 29 close: -400 - (-200)
  assert.match($("historyTotal").innerHTML, /-\$200/);
  assert.match($("historyNote").textContent, /since Oct 29, 2026/);
});

const click = (element, sort) =>
  element.handlers.click({ target: { closest: () => ({ dataset: { sort }, textContent: sort }) } });
const order = (html, names) => names.slice().sort((a, b) => html.indexOf(a) - html.indexOf(b));

test("sector headers: first click sorts, second reverses, third clears", async () => {
  const rows = [
    position({ conid: "1", symbol: "BIG", market_value: "9000", day_change_value: "1" }),
    position({ conid: "2", symbol: "UP", market_value: "500", day_change_value: "300" }),
    position({ conid: "3", symbol: "DOWN", market_value: "100", day_change_value: "-50" }),
  ];
  const sectors = { sectors: { BIG: "Alpha", UP: "Bravo", DOWN: "Charlie" } };
  const $ = await render({ date: "20261106", positions: rows, lots: [] }, sectors);
  const names = ["Alpha", "Bravo", "Charlie"];
  const list = () => order($("sectorList").innerHTML, names);
  assert.deepEqual(list(), ["Alpha", "Bravo", "Charlie"]); // default: gross exposure
  click($("sectorHead"), "day");
  assert.deepEqual(list(), ["Bravo", "Alpha", "Charlie"]); // biggest day change first
  click($("sectorHead"), "day");
  assert.deepEqual(list(), ["Charlie", "Alpha", "Bravo"]); // reversed
  click($("sectorHead"), "day");
  assert.deepEqual(list(), ["Alpha", "Bravo", "Charlie"]); // cleared
});

test("position headers: third click returns to the Sort dropdown order", async () => {
  const rows = [
    position({ conid: "1", symbol: "MMM", market_value: "900" }),
    position({ conid: "2", symbol: "ZZZ", market_value: "300" }),
    position({ conid: "3", symbol: "AAA", market_value: "100" }),
  ];
  const $ = await render({ date: "20261106", positions: rows, lots: [] });
  const head = $("query:#holdings thead");
  const list = () => order($("rows").innerHTML, [">MMM<", ">ZZZ<", ">AAA<"]);
  assert.deepEqual(list(), [">MMM<", ">ZZZ<", ">AAA<"]); // default: market value
  click(head, "symbol");
  assert.deepEqual(list(), [">AAA<", ">MMM<", ">ZZZ<"]);
  click(head, "symbol");
  assert.deepEqual(list(), [">ZZZ<", ">MMM<", ">AAA<"]);
  click(head, "symbol");
  assert.deepEqual(list(), [">MMM<", ">ZZZ<", ">AAA<"]); // cleared back to market value
  click(head, "symbol");
  assert.deepEqual(list(), [">AAA<", ">MMM<", ">ZZZ<"]); // starts over
});

test("history stays off until IBKR sends NAV", async () => {
  const $ = await render({ date: "20261106", positions: [position({})], lots: [] }, undefined,
    { available: false, points: [], reason: "Portfolio history turns on once the IBKR Flex query includes daily NAV." });
  assert.match($("historyChart").innerHTML, /turns on once the IBKR Flex query includes daily NAV/);
  assert.doesNotMatch($("historyChart").innerHTML, /<svg/);
  assert.equal($("historyTotal").textContent, "");
});

test("dashboard donut shows sector share of gross exposure, top 6 plus Other", async () => {
  const names = ["A", "B", "C", "D", "E", "F", "G", "H"];
  const rows = names.map((n, i) => position({ conid: String(i), symbol: n, market_value: String((8 - i) * 100) }));
  const sectors = { sectors: Object.fromEntries(names.map((n) => [n, `Sector ${n}`])) };
  const $ = await render({ date: "20261106", positions: rows, lots: [] }, sectors);
  const legend = $("legend").innerHTML;
  assert.match(legend, /Sector A<\/span><strong>22.22%/); // 800 of 3,600 gross
  assert.match(legend, /Other \(2 sectors\)<\/span><strong>8.33%/);
  assert.doesNotMatch(legend, /Sector G/);
  assert.equal($("classCount").textContent, 8);
});

test("sorting sectors never opens a sector by itself", async () => {
  const $ = await render({ date: "20261106", positions: [position({})], lots: [] });
  click($("sectorHead"), "day");
  assert.doesNotMatch($("sectorList").innerHTML, /<details class="sector-row" open/);
});

const RISK = {
  available: true, as_of: "20261106", prices_through: "2026-11-06", scenarios: 500, method: "1-day historical simulation",
  fund: { nlv: 6000000, var_95: 57271, var_99: 112667, es_97_5: 118385, var_95_pct_nlv: 0.95, var_99_pct_nlv: 1.88,
          es_97_5_pct_nlv: 1.97, annual_vol: 614900, annual_vol_pct_nlv: 10.2, sp500_down_1pct: -36513 },
  accounts: [{ id: "U9000002", nlv: 2797532, var_95: 34476, var_95_pct_nlv: 1.23, var_99: 60000, es_97_5: 70000 }],
  top_contributors: [{ symbol: "QQQ", sector: "Diversified Funds", exposure: 662661, es_contribution: 25891 }],
  sectors: [{ sector: "Information Technology", es_contribution: 42602 }],
  worst_days: [{ date: "2025-04-04", pnl: -197768 }],
  coverage: { modelled_pct: 96.88, excluded: [{ symbol: "AAA 261120C", account: "U1", value: 500, reason: "options/warrants are not modelled yet" }] },
};

test("risk tab shows VaR, account risk, contributors, stress days and exclusions", async () => {
  const $ = await render({ date: "20261106", positions: [position({})], lots: [] }, undefined, undefined, RISK);
  assert.equal($("var95").textContent, "$57,271");
  assert.equal($("var95Pct").textContent, "0.95% of NLV · exceeded 1 day in 20");
  assert.match($("spMove").innerHTML, /-36,513/);
  assert.match($("riskAccounts").innerHTML, /<td>U9000002<\/td><td>Tier One Acct<\/td>/);
  assert.match($("riskContributors").innerHTML, /QQQ.*21.87%/); // 25,891 of 118,385
  assert.match($("riskWorst").innerHTML, /Apr 4, 2025.*-197,768/);
  assert.match($("riskExcluded").innerHTML, /options\/warrants are not modelled yet/);
  assert.equal($("riskCoverage").textContent, "96.88% of gross exposure modelled; 1 positions left out");
});

test("risk tab says so when the report has not run", async () => {
  const $ = await render({ date: "20261106", positions: [position({})], lots: [] });
  assert.equal($("riskAsOf").textContent, "Risk report not available");
  assert.equal($("var95").textContent, "");
});

test("options get their own group and the underlying's sector", async () => {
  const option = position({ conid: "9", symbol: "IWM   261120P00280000", description: "IWM 20NOV26 280 P", asset_category: "OPT", multiplier: "100" });
  const warrant = position({ conid: "8", symbol: "TMSWW", description: "TMSW 17APR30 11.5 C", asset_category: "WAR" });
  const $ = await render({ date: "20261106", positions: [position({}), option, warrant], lots: [] },
    { sectors: { AAA: "Energy", IWM: "Diversified Funds", TMSWW: "Unclassified", TMSW: "Information Technology" } });
  $("groupBy").value = "asset";
  $("groupBy").handlers.change();
  assert.match($("rows").innerHTML, /Options<\/button>/);
  const html = $("sectorList").innerHTML;
  assert.match(html, /<strong>Diversified Funds<\/strong>/);
  assert.match(html, /<strong>Information Technology<\/strong>/);
  assert.doesNotMatch(html, /Unclassified/);
});

test("trade history stays hidden while the server keeps it off", async () => {
  const $ = await render({ date: "20261106", positions: [position({})], lots: [] });
  assert.equal($("tradeList").innerHTML, ""); // the trades endpoint 404s, so nothing renders
  const page = readFileSync(new URL("../ui/index.html", import.meta.url), "utf8");
  assert.match(page, /id="tradeLog" hidden/);
});

test("trade history lists executions with names and USD values once enabled", async () => {
  const trades = { trades: [
    { trade_date: "20261105", account_id: "U9000005", buy_sell: "SELL", symbol: "GDX", quantity: "-100",
      trade_price: "83.11", currency: "USD", fx_rate_to_base: "1", proceeds: "8311", realized_pnl: "3412.32" },
  ] };
  const $ = await render({ date: "20261106", positions: [position({})], lots: [] }, undefined, undefined, undefined, { "api/v1/trades": trades });
  assert.equal($("tradeLog").hidden, false);
  assert.match($("tradeList").innerHTML, /<td>Quant<\/td><td>SELL<\/td><td>GDX<\/td><td class="num">-100<\/td><td class="num">\$83.11<\/td><td class="num"><span class="">8,311<\/span><\/td><td class="num"><span class="">3,412<\/span>/);
});

test("what-if reads spreadsheet paste and CSV files with any header order", async () => {
  const $ = await render({ date: "20261106", positions: [position({})], lots: [] });
  const { wiParse } = $.api;
  assert.deepEqual(wiParse('AAPL\t"1,000"'), [{ symbol: "AAPL", quantity: "1000", account: "" }]); // quoted spreadsheet cell
  assert.deepEqual(wiParse("NVDA\t100\tQuant\nGLD\t-50\t"),
    [{ symbol: "NVDA", quantity: "100", account: "Quant" }, { symbol: "GLD", quantity: "-50", account: "" }]);
  assert.deepEqual(wiParse('Account,Ticker,Shares\r\nU9000002,AAPL,"1,000"\r\nU9000005,MSFT,(25)\r\n'),
    [{ symbol: "AAPL", quantity: "1000", account: "U9000002" }, { symbol: "MSFT", quantity: "-25", account: "U9000005" }]);
  assert.deepEqual(wiParse("symbol;quantity\nSAP;10"), [{ symbol: "SAP", quantity: "10", account: "" }]);
});

test("what-if accepts account names as well as numbers", async () => {
  const $ = await render({ date: "20261106", positions: [position({})], lots: [] });
  assert.equal($.api.wiAccountId("quant"), "U9000005");
  assert.equal($.api.wiAccountId("U9000002"), "U9000002");
  assert.equal($.api.wiAccountId("Unknown Book"), "Unknown Book"); // the server rejects that row with a reason
});

test("what-if results lead with before/after cards and list every trade and left-out row", async () => {
  const $ = await render({ date: "20261106", positions: [position({})], lots: [] });
  const side = (v) => ({ var_95: v, var_99: v * 2, es_97_5: v * 2.1, sp500_down_1pct: -v / 2 });
  $.api.wiRender({
    as_of: "20261106", scenarios: 500,
    trades: [{ row: 1, symbol: "NVDA", account: "U9000005", quantity: 100, price: 186.64, fx_to_usd: 1, value: 18664,
      stand_alone_var_95: 770, es_contribution: 1160, sector: "Information Technology" }],
    rejected: [{ row: 2, symbol: "ZZZ", reason: "no LSEG daily price history" }],
    basket: { net_value: 18664, gross_value: 18664, stand_alone_var_95: 770, es_contribution: 1160 },
    fund: { before: side(57205), after: side(57825) },
    accounts: [{ id: "U9000005", trades: 1, before: side(1000), after: side(1500) }],
    sectors: [{ name: "Information Technology", before_usd: 800000, after_usd: 818664, before_pct: 14.96, after_pct: 15.25 }],
    gross_before: 5340000, gross_after: 5358664,
    worst_days: [{ date: "2025-04-04", before: -216627, trades: -1373, after: -218000 }],
  });
  const html = $("wiResult").innerHTML;
  assert.match(html, /<label>Fund VaR 95%<\/label><strong>\$57,825<\/strong>\s*<small>was \$57,205 · <b class="wi-change">\+620 \(\+1.08%\)/);
  assert.match(html, /Row 2 ZZZ: no LSEG daily price history/);
  assert.match(html, /<td>Quant<\/td><td class="num">1<\/td>\s*<td class="num">1,000<\/td><td class="num">1,500<\/td><td class="num">\+500<\/td>/);
  assert.match(html, /\+0.29 pts/);
  assert.match(html, /\$800,000 · 14.96%/);
  assert.match(html, /-218,000/);
});

test("the same symbol and account entered twice becomes one row with the quantities added", async () => {
  const $ = await render({ date: "20261106", positions: [position({})], lots: [] });
  const t = (symbol, quantity, account = "") => ({ symbol, quantity, account });
  assert.deepEqual($.api.wiMerge([
    t("NVDA", "100", "Quant"), t("GLD", "-50"), t("nvda", "1,000", "u9000005"), t("NVDA", "25"), t("GLD", "50"), t("AAPL", ""), t("AAPL", ""),
  ]), [t("NVDA", "1100", "Quant"), t("GLD", "0"), t("NVDA", "25"), t("AAPL", ""), t("AAPL", "")]);
  assert.deepEqual($.api.wiMerge([t("MSFT", "(25)"), t("MSFT", "$100")]), [t("MSFT", "75")]); // accounting negatives
});

test("dropping an Excel or Numbers file asks for CSV instead of filling junk rows", async () => {
  const $ = await render({ date: "20261106", positions: [position({})], lots: [] });
  await $.api.wiOpen({ name: "trades.xlsx", text: async () => "PK\u0003\u0004\u0014\0\0\0\b\0��" });
  assert.match($("wiResult").innerHTML, /trades\.xlsx is a spreadsheet file, not CSV/);
  await $.api.wiOpen({ name: "notes.csv", text: async () => "symbol,quantity\n" });
  assert.match($("wiResult").innerHTML, /No trades found in notes\.csv/);
});

test("what-if will not run while a row is missing its quantity or symbol", async () => {
  const $ = await render({ date: "20261106", positions: [position({})], lots: [] });
  const input = (value) => ({ value, invalid: false, classList: { toggle(_, on) { input.last = this; this.owner.invalid = on; } } });
  const row = (...values) => {
    const inputs = values.map((v) => { const i = input(v); i.classList.owner = i; return i; });
    return { inputs, querySelectorAll: () => inputs };
  };
  const rows = [row("NVDA", "100", ""), row("GLD", "", ""), row("", "50", ""), row("AAPL", "abc", "Quant"), row("", "", "")];
  $("wiRows").children = rows;
  assert.deepEqual($.api.wiProblems(), ["Row 2: enter a quantity", "Row 3: enter a symbol", "Row 4: enter a quantity (a non-zero number)"]);
  assert.deepEqual(rows.map((r) => r.inputs.slice(0, 2).map((i) => i.invalid)),
    [[false, false], [false, true], [true, false], [false, true], [false, false]]);
});

test("heatmap tiles fill the box with areas in proportion to value", async () => {
  const $ = await render({ date: "20261106", positions: [position({})], lots: [] });
  const items = [50, 25, 10, 8, 4, 2, 1].map((size, i) => ({ id: i, size }));
  const rects = $.api.hmSquarify(items, 0, 0, 300, 200);
  assert.equal(rects.length, items.length);
  for (const r of rects) {
    assert.ok(Math.abs(r.w * r.h - (r.item.size / 100) * 60000) < 1e-6);
    assert.ok(r.x >= -1e-9 && r.y >= -1e-9 && r.x + r.w <= 300 + 1e-9 && r.y + r.h <= 200 + 1e-9);
  }
  for (const a of rects) for (const b of rects) if (a !== b)  // no two tiles overlap
    assert.ok(a.x + a.w <= b.x + 1e-9 || b.x + b.w <= a.x + 1e-9 || a.y + a.h <= b.y + 1e-9 || b.y + b.h <= a.y + 1e-9);
  assert.deepEqual($.api.hmSquarify([], 0, 0, 300, 200), []);
});

test("heatmap combines a stock across accounts and colours by price move", async () => {
  const $ = await render({ date: "20261106", positions: [
    position({ account_id: "U1", symbol: "AAA", market_value: "1000", day_change_value: "20", day_change_pct: "2" }),
    position({ account_id: "U2", symbol: "AAA", market_value: "500", day_change_value: "10", day_change_pct: "2" }),
    position({ account_id: "U2", conid: "2", symbol: "BBB", market_value: "-300", day_change_pct: "-1.5", day_change_value: "9" }),
  ], lots: [] });
  const [sector] = $.api.hmSectors();
  assert.deepEqual(sector.tiles.map((t) => [t.symbol, t.value, t.size, t.accounts.size]), [["AAA", 1500, 1500, 2], ["BBB", -300, 300, 1]]);
  assert.equal($.api.hmColor(0), "rgb(72,78,92)");
  assert.equal($.api.hmColor(5), "rgb(3,152,85)"); // past +3% stays full green
  assert.equal($.api.hmColor(-3), "rgb(217,45,32)");
  assert.equal($.api.hmColor(null), "#b8bec9");
  $.api.renderHeatmap();
  assert.equal(($("heatmap").innerHTML.match(/class="hm-tile"/g) || []).length, 2);
  assert.match($("heatmap").innerHTML, /BBB ·S/);
  assert.equal($("hmNote").textContent, "2 holdings in 1 sectors");
});

test("positions CSV holds every holding, ignoring filters, with names and quoted text", async () => {
  const $ = await render({ date: "20261106", positions: [
    position({ account_id: "U9000001", symbol: "AAA", description: 'Acme, "Inc"', market_value: "500" }),
    position({ account_id: "U9000002", conid: "2", symbol: "BBB", market_value: "-900", day_change_price: "" }),
  ], lots: [] });
  $("accountFilter").value = "U9000001";
  const [head, ...rows] = $.api.positionsCsv();
  assert.equal(head.length, 17);
  assert.deepEqual(rows.map((r) => [r[0], r[2], r[3], r[11]]), [["AAA", "U9000001", "Lifeboat", "500"], ["BBB", "U9000002", "Tier One Acct", "-900"]]);
  assert.equal(rows[1][9], ""); // no previous close: blank, not 0
  assert.equal($.api.csvCell('Acme, "Inc"'), '"Acme, ""Inc"""');
});
