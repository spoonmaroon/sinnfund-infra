// Runs ui/app.js against fake API data and checks what the dashboard renders.
// Run: node --test services/flex-web-live/tests/
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

const APP = readFileSync(new URL("../ui/app.js", import.meta.url), "utf8");

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

async function render(report, sectors = { sectors: {} }, history = { points: [] }, risk = { available: false, reason: "not run" }) {
  const elements = new Map();
  const byId = (id) => elements.get(id) ?? elements.set(id, fakeElement()).get(id);
  const responses = {
    "api/v1/portfolio": { totals: {}, allocation: [] },
    "api/v1/report": report,
    "api/v1/sectors": sectors,
    "api/v1/prices/validation": { coverage_pct: 100 },
    "api/v1/history": history,
    "api/v1/risk": risk,
    "static/account-names.json": { U9000001: "Lifeboat", U9000002: "Tier One Acct", U9000004: "Factor Investing - Short Interest", U9000005: "Quant" },
  };
  const context = {
    document: {
      getElementById: byId, body: fakeElement(), addEventListener() {},
      querySelector: (selector) => byId(`query:${selector}`), querySelectorAll: () => [],
    },
    window: { scrollTo() {} },
    fetch: async (url) => ({ ok: url in responses, json: async () => responses[url] }),
    console,
  };
  new Function(...Object.keys(context), APP)(...Object.values(context));
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
