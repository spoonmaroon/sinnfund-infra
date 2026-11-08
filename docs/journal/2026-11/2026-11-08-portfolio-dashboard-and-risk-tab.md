# 2026-11-08 — Portfolio dashboard revamp, data fixes and Risk management tab

**Component / branch / issue:** `services/flex-web-live` (UI, API, `risk/`), `services/flex-ingest` (branch `claude/serene-tesla-3decfb`), `deploy/nginx`, `deploy/risk-pipeline`

## Goal and acceptance criteria

- **User-visible behavior:** one portfolio site combining previous-close and delayed prices; correct totals and day change; a Risk management tab with VaR; usable by many viewers at once.
- **Definition of done:** deployed to `/live/` and `/close/`, verified in a browser, covered by tests, documented.

## Implementation notes

- **UI:** moved the local preview into `flex-web-live/ui/`, served at `/` with relative API calls (ADR-0027). Removed the Data status page, the fake history curve, stat-card icons and nav glyphs. Added the accounts table modelled on the fund's account sheet, a sector donut, sortable sector columns (click three times to clear), and the Risk management tab.
- **Data fixes found by auditing the columns:**
  - non-USD positions were summed as dollars (INC-0014);
  - day change compared a price with itself on weekends (INC-0014);
  - IBKR left three accounts out of the Nov 6 file (INC-0013, ADR-0031);
  - the dashboard ignored cash, so NLV is now positions + ending cash (ADR-0028).
- **History:** built a mark-to-market P&L curve from 20 daily statements, then switched it off until NAV is in the Flex query, because P&L without cash and flows misleads (ADR-0028).
- **Load:** shared parse with stale-while-refresh and pre-gzipped JSON; 30 simultaneous viewers went from about 5 s to under 1 s per page load.
- **Risk:** found the Stage 1–4 VaR pipeline stuck since Oct 22 (INC-0015). Replaced it with a Riskfolio-Lib job in a separate venv (ADR-0029). Prices came from a two-year backfill with the operator's own LSEG login, using ISIN lookup for stocks outside the feed's symbol map (ADR-0030); coverage went from 89% to 97% of gross. yfinance was installed, then removed at the fund's request.
- **Ingest:** deployed the partial-statement retry from commit `4a647a9`; it re-downloads Nov 6 on the next timer run.
- **Options and trades:** options got their own group and their underlying's sector; option repricing from LSEG is ready in flex-web-live and waits on the feed (fix script step 5). Trade history (863 executions from the Flex `Trade` rows) is built but hidden behind `TRADE_LOG=1`.
- **Kept apart on review:** the landing page stays at `/` (no redirect) and `/close/` is again the separate close-only dashboard, for debugging and fallback; its landing card was removed. Later the same day the landing page's boot animation was removed at the fund's request, and the Prediction Signals card went live, linking to the GitHub quick start from "Save your sender key" (status from `/prediction/openapi.json`; the API has no `/healthz`).
- **Fix script run (operator sudo granted):** the LSEG history API is live on 8092 with 320 symbols; the refresh now fails only when most symbols fail. The option RICs only took effect after a feed fix (the Flex resolver replaced the configured list instead of merging); the feed now carries 361 symbols, including the five options and the accounts missing from the 2026-11-06 file.
- **Access limits met:** operator sudo covers only five service restarts (exact `sudo -n /usr/bin/systemctl restart <unit>.service` form). Nginx, `/var/www`, `lseg-history` and the Stage units need an admin; `deploy/risk-pipeline/fix-risk-pipeline.sh` and the nginx root redirect are staged for one.

## Validation

| Test | Expected | Observed | Result |
| --- | --- | --- | --- |
| Python unit tests (`tests/`) | pass | 22 tests OK | Pass |
| Dashboard tests (`node --test tests/`) | pass | 18 tests OK | Pass |
| Risk job tests (`risk/test_risk_job.py`, risk venv) | VaR matches sorted losses; contributions sum to ES | 5 tests OK | Pass |
| Live NLV vs fund sheet (10/31) | cash-only and long/short books reconcile | Quant exact; others within market moves | Pass |
| Load test, 30 viewers × 3 | no errors | 720 requests, 0 errors, p50 ≤ 1.1 s | Pass |
| Fund VaR vs retired Stage 3 (Oct 22) | same order of magnitude | $57k vs $65k | Pass |

**Intentionally not handled:** options/warrants in risk; FX moves in risk; official NAV/TWR; LSEG history service (needs sudo); daily risk schedule (user crontab not yet installed).

## What I learned — my own words

- **New technical concept:** [ ]
- **Design or test habit I will reuse:** [ ]
- **What I would change in a rewrite:** [ ]

## Follow-up and evidence

- **Next actions:** admin runs `fix-risk-pipeline.sh` (now also adds option prices) ; add the NAV fields to the Flex query; install the risk crontab; confirm the Nov 10 ingest replaces the Nov 6 partial; name the two accounts missing from the fund sheet; merge the ingest branch.
- **Evidence:** deploy backups under `/opt/flex-web-live/deploy-backups/` and `/opt/flex-ingest/deploy-backups/`.
