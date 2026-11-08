# ADR-0032: Keep portfolio data in files for now; add a database when a named trigger is hit

- **Date:** 2026-11-08
- **Status:** Accepted — no database yet; revisit at the first trigger below

## Context

The portfolio site, risk tab and What if page read everything from files that are written once and never edited:

| Data | Where it lives | Writer |
|---|---|---|
| IBKR positions, cash, trades, flows | Daily Flex XML archives, `/var/lib/flex-ingest/flex/data/YYYYMMDD/` | `flex-ingest` (daily) |
| Year-start NLV and earlier flows | `/var/lib/flex-ingest/flex/ytd/<year>/` | `ytd/pull_year_start.py` (once a year) |
| Daily closes (holdings + S&P 500) | LSEG history service SQLite, `/var/lib/lseg-history/prices.sqlite3` | `lseg-history-refresh` (daily) |
| Risk report and What if inputs | `risk/latest.json`, `risk/scenarios.json` | `risk_job.py` |
| Account names | `ui/account-names.json` (server only, not in git) | Operator |

There is one writer per file, no user edits, and the largest read is about 40 days of XML. A database would add a service to run, back up and secure without making any page faster or more correct today.

The fund still wants to: record LSEG ticks every second, keep risk and portfolio history, let people save work (trade baskets, limits, notes), and let outside users query stored data with API keys. Those need a database.

## Options considered

- **Postgres now for everything.** Most flexible, but nothing to put in it yet that files do not already serve.
- **ClickHouse now for ticks.** No tick recording exists yet.
- **Files now, with written triggers for each database.** Chosen.

## Decision

Keep files until one of these triggers is hit, then add the matching database:

| Trigger | Database | Why |
|---|---|---|
| Recording LSEG ticks (per second) or intraday bars | **ClickHouse** | Millions of rows a day; column store with compression and fast time-range queries |
| Anything people create and edit: saved What if baskets, trade ideas, limits, notes, approvals | **Postgres** | Concurrent writes need transactions and an audit trail |
| Questions that re-parse a year of XML: NAV and P&L per account per day, holdings on a date, sector P&L over time | **Postgres** | One load per day instead of a parse per request |
| Outside users querying stored data with API keys | **Postgres** (reference data) / **ClickHouse** (prices, ticks) | Access by table and row, not by file |
| Intraday risk (VaR every few minutes, every run kept) | **Postgres** (results) | Queryable history of risk runs |
| The LSEG history SQLite needs more than one writer or more than ~5,000 symbols | Move it to **ClickHouse** or Postgres | SQLite allows one writer at a time |

## Rules that keep the move cheap

- **Raw sources are immutable.** Flex archives and LSEG pulls are never edited, so any database can be loaded from them from scratch.
- **Rows carry IBKR's and LSEG's own keys** (account ID, `conid`, `transactionID`, report date, RIC), which become primary keys.
- **One loader per source** in `web-live.py` (`load_report`, `load_history`, `load_trades`, `load_ytd`, `load_scenarios`). Switching a source to SQL changes that loader, not the API or the pages.
- **Money stays in strings or Decimal until summed**, matching how Flex reports it, so nothing is lost converting to `numeric` columns.

## Consequences

- Nothing new to operate or back up today.
- Each request that needs history (YTD flows, trade log) re-reads archives; cached for an hour. Fine at about 20–250 daily files; a trigger above when it is not.
- The first likely trigger is saved What if baskets or per-account daily NAV, both Postgres.

Links: ADR-0012 (LSEG history SQLite), ADR-0027, ADR-0029, `services/flex-web-live/README.md`.
