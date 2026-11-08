# Portfolio dashboard and API (`flex-web-live`)

The single read-only portfolio service. It reads the latest immutable IBKR Flex
archive, reprices stocks from one cached LSEG snapshot, and serves both the
dashboard UI and its JSON API. It never writes to the archives and exposes no
write endpoints.

Public route: `https://sinnfund.jkoh.dev/live/`, linked from the landing page at
`/`. The separate close-only dashboard at `/close/` (`services/flex-web`) stays
live for debugging and as a fallback; it is not linked from the landing page.

## Dashboard pages

| Page | What it shows |
|---|---|
| Dashboard | Net liquidation value (positions + cash), unrealized P&L, day change, accounts table (NLV, % allocation, positions, cash, day change, P&L), sector allocation donut. Portfolio history is off until IBKR sends daily NAV. |
| Positions | Every holding with price, day change, market value, cost, gain/loss and price source; expandable tax lots; grouping, filters and sortable columns. |
| Sectors | Gross exposure, net value, day change and unrealized P&L per sector; sortable columns; holdings per sector. |
| What if | Basket of proposed trades (typed, pasted from a spreadsheet or a CSV) and their effect on fund and account risk; see `risk/README.md`. |
| Risk management | 1-day VaR 95/99%, expected shortfall 97.5%, S&P 500 sensitivity, account risk, top risk contributors, risk by sector, worst historical days, model exclusions. See [`risk/README.md`](risk/README.md). |

Column headers sort on the first click, reverse on the second and return to the
default order on the third.

## Endpoints

| Endpoint | Purpose |
|---|---|
| `GET /` | Dashboard page (`ui/index.html`); assets under `/static/` |
| `GET /api/v1` | Endpoint list |
| `GET /api/v1/portfolio` | Totals (positions, cash, NLV, day change), accounts, allocation |
| `GET /api/v1/report` | Every position and tax lot, plus `cash` by account and `carried_forward` |
| `GET /api/v1/positions` | Paginated position rows (`q`, `account`, `offset`, `limit` ≤ 250) |
| `GET /api/v1/sectors` | Symbol → sector map (`sectors.json`) |
| `GET /api/v1/history` | `{"available": false}` until the Flex archive carries daily NAV totals |
| `GET /api/v1/risk` | Latest risk report written by `risk/risk_job.py` |
| `POST /api/v1/risk/whatif` | Pre-trade check for a basket: `{"trades": [{symbol, quantity, account}]}`; risk before and after (see `risk/README.md`) |
| `GET /api/v1/prices/validation` | Close-versus-delayed coverage and gap checks |
| `GET /api/v1/trades` | Every execution in the archives, newest first. **Hidden:** 404 unless the service runs with `TRADE_LOG=1`; the page's Trade history card stays hidden until then. Not listed in `/api/v1`. |
| `GET /healthz` | Archive availability |

## How the numbers are built

- **Currency.** Flex reports values in each position's own currency. Every total
  converts with the row's `fx_rate_to_base` (IBKR's rate). Prices stay in local
  currency and are labelled with it (`¥1,216`, `DKK 209.90`). See INC-0014.
- **Day change.** If the delayed LSEG quote is from a session after the statement
  date (market open, or closed but not yet in Flex), the change is delayed price
  minus statement close. Otherwise it is statement close minus the previous
  statement's close, so weekends show the last session's move and close-only
  foreign listings still get a day change. Rows: `previous_close_price`,
  `day_change_price`, `day_change_pct`, `day_change_value` (USD), `day_change_basis`.
  A quote stamped on a weekend or before the 09:30 ET open counts as the last
  session's, so an off-hours feed refresh does not zero the day change
  (`session_date()`; exchange holidays are not rolled back yet).
- **Tickers with a space.** The LSEG feed cannot key a ticker with a space, so
  stocks are looked up with a dot (IBKR `BRK B` → feed `BRK.B=/BRKb`) and listed
  options without spaces (`IWM261120P00280000`).
- **Net liquidation value.** Positions in USD plus each account's
  `CashReportCurrency[currency=BASE_SUMMARY].endingCash`. It excludes accruals and
  is not official NAV (INC-0007, ADR-0028). Accounts with cash but no positions
  are listed; accounts with neither are hidden.
- **Accounts left out of a statement.** IBKR sometimes excludes accounts from a
  day's file. Accounts with no `FlexStatement` in the latest file are carried
  forward from the previous file, repriced like every other row, and named in
  `carried_forward` and on the status pill (INC-0013, ADR-0031).
- **Account names** come from `ui/account-names.json` (account number → name,
  from the fund's account list; IBKR's `acctAlias` is empty). It is kept on the
  server and out of git because the repo is public. Without it the dashboard
  shows account numbers only.
- **Options and warrants** are grouped as Options / Warrants and take their
  underlying's sector (first word of the symbol: `IWM   261120P…` → IWM) when
  they have none of their own. Listed options reprice from the LSEG feed when it
  carries them, keyed by the OCC symbol without spaces (`IWM261120P00280000`);
  `deploy/risk-pipeline/fix-risk-pipeline.sh` step 5 adds the current five (needs
  an admin). Warrants stay on the statement price.
- **Sectors** come from `sectors.json`, rebuilt with
  `python3 build_sectors.py <saved /api/v1/report response>`.

The original statement fields are always kept on each row (`close_mark_price`,
`close_market_value`, `close_unrealized_pnl`). When an LSEG quote passes the
instrument and price-ratio checks the row also carries `delayed_*`, `quote_*`
and `price_validation`, and the legacy `mark_price`, `market_value` and
`unrealized_pnl` hold the delayed values.

## Many viewers at once

- `ReportCache` shares one parse for 30 seconds. While one request refreshes,
  others get the previous snapshot instead of waiting; a failed refresh keeps
  serving the last good snapshot.
- JSON responses are encoded and gzipped once per refresh (`shared_json`).
- Measured on 2026-11-08 (2 vCPU, one Uvicorn worker): 30 simultaneous viewers,
  720 requests, no errors, page assets p50 ≈ 0.7 s, full report p50 ≈ 1.1 s
  (previously ≈ 5 s).

## Tests

```bash
# from services/flex-web-live, with the service venv
PYTHONPATH=. python -m unittest tests/test_combined_prices.py tests/test_dashboard_routes.py \
  tests/test_day_change.py tests/test_concurrency.py tests/test_position_changes.py \
  tests/test_whatif.py
node --test tests/            # dashboard rendering, no npm packages
# risk job (needs the risk venv on the server)
/opt/flex-web-live/risk-venv/bin/python -m unittest risk/test_risk_job.py
```

`tests/dashboard.test.mjs` runs `ui/app.js` against fake API data with a minimal
DOM stand-in and checks totals, FX, day change, accounts, sectors, sorting, the
history gate, the risk tab, options grouping and the hidden trade history.
`tests/test_position_changes.py` writes real Flex archive files, then buys, sells
and resizes positions day by day and checks the API serves each change.

## Deploy

Uvicorn listens on `127.0.0.1:8002` (`deploy/flex-web-live.service`, user `svc-flex`).

```bash
# back up, copy, restart (operators in trading-dev can write /opt/flex-web-live)
ssh sinnfund 'cd /opt/flex-web-live && B=deploy-backups/<label>-$(date -u +%Y%m%dT%H%M%SZ) && mkdir -p $B && cp -a web-live.py portfolio_api.py parse_flex_positions_live.py ui $B/'
rsync -a web-live.py portfolio_api.py parse_flex_positions_live.py sectors.json ui sinnfund:/opt/flex-web-live/
ssh sinnfund 'sudo -n /usr/bin/systemctl restart flex-web-live.service'
```

The sudoers rule only matches that exact form (full path and `.service`).
Static-only changes under `ui/` and `sectors.json` need no restart.
