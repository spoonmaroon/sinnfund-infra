# ADR-0029: Replace the Stage 1–4 VaR pipeline with a Riskfolio-Lib risk job behind the dashboard

- **Date:** 2026-11-08
- **Status:** Accepted — deployed; daily schedule pending. Supersedes ADR-0015 and ADR-0019; extends ADR-0021.

## Context

The Stage 1–4 pipeline (outside this repository) last produced a report for 2026-10-22. Stage 3 loaded Stage 1 from a path that does not exist, the Stage 2/3 timers were never enabled, and its price source was failing (INC-0015). It used 100 scenarios, could not express risk as a share of NAV, and lived in a separate dashboard. Operators wanted a Risk management tab in the portfolio site built on maintained open-source tools.

## Options considered

- Repair the Stage 1–4 pipeline (needs admin access to its units and data).
- Hand-write VaR in the dashboard service.
- Use an open-source library (Riskfolio-Lib, skfolio, arch, quantstats) in a separate job and serve its output.

## Decision

Add `services/flex-web-live/risk/risk_job.py`, run in its own venv with **Riskfolio-Lib**. It reads positions, cash and FX from the portfolio API, applies the last **500** daily price moves to today's holdings, and writes `risk/latest.json` with fund and account VaR 95/99%, expected shortfall 97.5%, per-position and per-sector risk contributions, worst days, S&P 500 sensitivity and an exclusion list. The dashboard serves it at `/api/v1/risk` and renders the Risk management tab. Retire the Stage 1–4 pipeline; it is left installed but no longer referenced.

## Rationale

Same holdings, FX and NLV as the dashboard, so the figures cannot drift. Riskfolio-Lib provides tested historical VaR/CVaR and risk contributions that add up to the fund total. A separate venv keeps the web service small (the risk venv is ~1.3 GB).

## Consequences and limitations

- Options and warrants are excluded (about $41k gross); FX moves are not modelled; stocks need 250 days of history.
- Historical simulation reacts slowly to new volatility regimes.
- Scheduling depends on a user crontab that is not yet installed.

## Follow-up / revisit criteria

Install the schedule. Add delta-based option exposure, FX moves, and an EWMA/GARCH volatility view (`arch`) when needed. Read prices from `lseg-history-api` once it runs (ADR-0030).

Links: `services/flex-web-live/risk/README.md`, ADR-0016, ADR-0017, ADR-0021, INC-0015.
