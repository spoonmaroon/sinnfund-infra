# ADR-0030: Backfill risk price history with an operator's LSEG login until the server's history service works

- **Date:** 2026-11-08
- **Status:** Partly superseded 2026-11-08 — the risk job reads the LSEG history service (8092) first; `prices.csv` remains only for listings the service does not carry

## Context

VaR needs about two years of daily closes. The server's LSEG history cache (ADR-0012) is readable only by `svc-lseg`, its refresh job failed, and its read API was never installed (INC-0015). IBKR statements only cover the days archived since 2026-10-09. An unofficial Yahoo source was considered and rejected by the fund.

## Options considered

- Wait for an admin to fix `lseg-history`.
- Use an unofficial public source (Yahoo via yfinance, Nasdaq's site).
- Use an operator's own licensed LSEG cloud login to download closes now.

## Decision

`services/flex-web-live/risk/lseg_backfill.py` (a `uv run --script` with `lseg-data` 2.1.1) runs on the operator's machine, reads credentials from the environment or `~/.config/research-keys`, downloads two years of `TRDPRC_1` daily closes for every symbol in the feed's map plus held stocks resolved by LSEG's ISIN lookup, and writes `prices.csv`, which is copied to `/opt/flex-web-live/risk/`. The risk job reads that file.

## Rationale

Licensed data the fund already uses, no new vendor, and the operator's credentials never reach the server or this repository.

## Consequences and limitations

- Prices only update when the operator re-runs the backfill; Refinitiv Workspace must be closed if it uses the same login.
- The file holds raw LSEG data: keep it private; only derived risk figures are published.

## Follow-up / revisit criteria

Switch `risk_job.py` to `lseg-history-api` on `127.0.0.1:8092` once `deploy/risk-pipeline/fix-risk-pipeline.sh` has been run and the refresh succeeds daily.

Links: ADR-0010, ADR-0012, ADR-0029, INC-0015.
