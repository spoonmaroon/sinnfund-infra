# Risk management report (`risk/`)

Builds the data behind the dashboard's **Risk management** tab. It replaces the
retired Stage 1–4 VaR pipeline (ADR-0029).

| File | Role |
|---|---|
| `risk_job.py` | Computes the report and writes `latest.json`, plus `scenarios.json` for the what-if check (atomic replace) |
| `lseg_backfill.py` | Downloads daily closes with an operator's own LSEG login into `prices.csv` |
| `test_risk_job.py` | Checks VaR, ES, contributions, exclusions and NLV on synthetic data |

On the server: `/opt/flex-web-live/risk/` holds the scripts, `prices.csv`,
`latest.json`, `scenarios.json` and `job.log`. None of the data files is committed or served raw.

## Method

1-day historical simulation with frozen holdings:

1. Positions, cash and FX come from the portfolio API (`/api/v1/report`), so the
   risk tab uses the same carried-forward accounts and USD conversion as the
   dashboard.
2. Each stock's USD exposure is revalued under each of the last **500** daily
   LSEG price moves. Fund and account P&L per scenario is the sum.
3. Riskfolio-Lib computes VaR 95%/99% (`RiskFunctions.VaR_Hist`), expected
   shortfall 97.5% (`CVaR_Hist`, Rockafellar–Uryasev), and each position's share
   of expected shortfall (`Risk_Contribution`, `rm="CVaR"`). Shares add up to the
   fund ES; sectors are summed from positions.
4. S&P 500 sensitivity is the dollar move of the modelled book for a 1% fall in
   IVV; worst days are the five worst scenario P&Ls; volatility is annualized
   (×√252).
5. Percentages use net liquidation value (positions + cash), not official NAV.

**Left out, and listed on the tab with a reason:** options and warrants; stocks
with fewer than 250 days of closes; stocks with no LSEG price in the last 10
days (delisted or stale); symbols LSEG has no history for. Foreign stocks use
local-currency moves; FX moves are not modelled.

Result on 2026-11-08 (holdings 2026-11-06, 500 days to 2026-11-06): VaR 95%
$57,271 (0.94% of NLV), VaR 99% $112,667, ES 97.5% $118,385, 96.9% of gross
exposure modelled.

## Pre-trade what-if

The **What if** box on the Risk management tab shows how one proposed trade
(symbol, quantity, optional account) changes VaR 95/99%, ES 97.5% and the S&P 500
sensitivity, for the fund and the chosen account, plus the trade's share of the
new ES, its stand-alone VaR, the sector weight change and its P&L on the fund's
five worst days.

- `risk_job.py` writes `scenarios.json`: every modelled symbol's 500 daily moves,
  its last close and the USD exposure per account (about 1.8 MB).
- `../whatif.py` (pure Python, in the web service) adds the trade as extra
  exposure and replays the same days. VaR/ES use Riskfolio's exact historical
  formulas, so "before" matches the report (checked in `test_risk_job.py`).
- `GET /api/v1/risk/whatif?symbol=&quantity=&account=` is read-only; nothing is
  stored. Symbols without LSEG daily history (and options/warrants) are refused
  with a reason.
- Limits: the trade is priced at the last LSEG close and assumed to be in USD;
  the data is as fresh as the last risk job run; liquidity (days to exit) and
  factor exposure are not covered yet (no volume or factor model stored).

## Environment

`/opt/flex-web-live/risk-venv` (Python 3.14) with `riskfolio-lib` 7.4.1 (and its
numpy, pandas, cvxpy). It is separate from the dashboard venv so the web service
stays small. Created with:

```bash
cd /opt/flex-web-live && python3 -m venv risk-venv && risk-venv/bin/pip install riskfolio-lib
```

## Run

```bash
cd /opt/flex-web-live && risk-venv/bin/python risk/risk_job.py
```

Scheduling is not installed yet. Intended user crontab on sinnfund (UTC; after
the 11:00 Flex download and after the US close):

```cron
45 11 * * 2-6 cd /opt/flex-web-live && risk-venv/bin/python risk/risk_job.py >> risk/job.log 2>&1
30 21 * * 1-5 cd /opt/flex-web-live && risk-venv/bin/python risk/risk_job.py >> risk/job.log 2>&1
```

## Price history

Interim (ADR-0030): an operator's own LSEG cloud login, run on their machine.
Close Refinitiv Workspace first if it uses the same login (one session per login).

```bash
scp sinnfund:/opt/lseg-feed/lseg-symbols.env .
curl -s https://sinnfund.jkoh.dev/live/api/v1/report -o report.json
uv run --script lseg_backfill.py lseg-symbols.env prices.csv --report report.json
scp prices.csv sinnfund:/opt/flex-web-live/risk/prices.csv
```

Credentials are read from `LSEG_APP_KEY`/`LSEG_USER`/`LSEG_PASSWORD` or
`~/.config/research-keys`, never printed. `--report` adds held stocks missing
from the feed's symbol map: LSEG's ISIN lookup resolves stocks, and US ETFs fall
back to the bare ticker or an exchange suffix. The 2026-11-08 run wrote 185,574
closes for 368 symbols (2024-10-08 to 2026-11-06).

Target: read the server's `lseg-history-api` (`127.0.0.1:8092`) once
`deploy/risk-pipeline/fix-risk-pipeline.sh` has been run by an admin (INC-0015).

LSEG data is licensed for the fund's own use: keep `prices.csv` private. The
dashboard only publishes computed risk figures.
