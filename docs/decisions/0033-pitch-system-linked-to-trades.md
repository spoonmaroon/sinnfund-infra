# ADR-0033: Build our own pitch system, signed in, linked to both funds' IBKR trades

- **Date:** 2026-11-11
- **Status:** Proposed — open questions below decide sign-in and visibility; nothing built yet

## Context

The fund wants a reason on record for every trade. Today pitches live wherever each analyst keeps them, desk leads collect them by hand, and nothing ties a pitch to the trades made because of it.

Wanted:

- Analysts submit pitches themselves; every pitch lands in one list without a desk lead uploading anything.
- PMs and desk leads browse pitches by fund, desk, analyst, ticker, date and status.
- Pitches link to the trades made under them, and trades without a pitch are visible.
- Both funds (Sinn and Rev) in one place once Rev's Flex token arrives.

Already in place:

- `flex-ingest` pulls several IBKR books (comma-separated `IBKR_TOKEN` / `IBKR_QUERY_ID`) into `YYYYMMDD/<book>.xml.gz`. Sinn's book is `main`.
- `flex-web-live` reads every book in a day folder, and `load_trades()` already reads IBKR's Trades section, deduplicated by `transactionID` (`/api/v1/trades`, hidden until `TRADE_LOG=1`).
- ADR-0005 keeps the dashboards read-only; ADR-0008 uses small FastAPI services behind Nginx; ADR-0032 names "anything people create and edit" as the trigger for Postgres.

## Options considered

| Option | Why not |
|---|---|
| An open-source project | None found that takes pitches from people and links them to trades. GitHub's "AI hedge fund" projects (virattt/ai-hedge-fund, TradingAgents, AutoHedge) use AI agents instead of analysts; portfolio trackers (Ghostfolio, Wealthfolio) have no pitches. Checked 2026-11-11. |
| Commercial research-management software | Paid per seat, built for firms; still needs IBKR trade import. |
| Pitches as Markdown files in a GitHub repo, reviewed as pull requests | Free and versioned, but every analyst needs git, and linking to trades still needs code. |
| Google Form into a Sheet | Easy to submit, but no roles, no trade links, data outside our audit trail. |
| Add writes to `flex-web-live` | Breaks ADR-0005 (dashboards stay read-only) and mixes sign-in into a public service. |
| **A separate `pitches` service with sign-in and Postgres** | **Chosen.** |

## Decision

### 1. A separate service

`services/pitches/` — FastAPI on `127.0.0.1:8004`, Nginx at `/pitches/`. Like the prediction API it accepts writes, but from signed-in people in a browser rather than API keys. The dashboards stay read-only and public as today; the Pitches tab and the trade log sit behind sign-in.

### 2. Sign-in and roles

- Nginx `auth_request` to `oauth2-proxy` with Google sign-in (GitHub as the fallback if analysts lack Google accounts). The service stores no passwords; it trusts the email Nginx passes only on the internal route.
- Roles come from a `people` table that an admin edits:

| Role | Can |
|---|---|
| Analyst | Create pitches; edit their own until approved; see pitches in their fund |
| Desk lead | Everything an analyst can, plus comment, approve or pass on pitches in their desk, link trades |
| PM | See and comment on every pitch in every fund; approve; link trades |
| Admin | Manage people, desks and funds |

- Signed-in email not in `people` → "ask an admin to add you", nothing else.

### 3. Data (Postgres)

| Table | Holds |
|---|---|
| `funds` | `main` → Sinn, `rev` → Rev (the Flex book name is the key) |
| `desks` | Name, fund, lead |
| `people` | Email, name, role, fund, desk |
| `pitches` | Fund, desk, author, title, tickers, long/short, thesis, catalysts, risks, target price, horizon, status, deck file or link, created/updated |
| `pitch_events` | Append-only: submitted, edited (old and new text), comment, status change, link, unlink — who and when |
| `trade_links` | Pitch, book, IBKR `transactionID`, who linked it, when |

- Status: `draft → submitted → approved | passed → active → closed`. Every change is a `pitch_events` row, so history is never overwritten.
- Decks: PDF only, up to 25 MB, stored under `/var/lib/pitches/decks/<pitch id>/`; or a link (Google Docs/Slides).

### 4. Trades and links

- Trades are read from the Flex archives with `flex-web-live`'s existing `read_trades`, keyed by book and `transactionID`. IBKR trade data arrives daily, so links are made after the fact (trade on Monday, linkable Tuesday).
- For each unlinked trade the service suggests open pitches with the same ticker and fund; a desk lead or PM confirms. Options and warrants match on the underlying.
- A "trades without a pitch" list per fund and desk.

### 5. Rev

When Rev's token arrives: add it to `flex-ingest`'s `.env` as book `rev`, add a Sinn / Rev / Both switch to the dashboards so totals are never mixed, and run the risk job per fund. Pitches already carry a fund from day one.

### 6. Backups

Pitches are the first data that cannot be rebuilt from IBKR or LSEG. Nightly `pg_dump` plus the decks folder, kept off the server, with a restore tested before launch.

## Build order

1. Sign-in (oauth2-proxy), `people`, roles.
2. Pitches: submit, list, filter, status, comments, decks.
3. Trade log on (behind sign-in), links, suggestions, trades without a pitch.
4. Rev book and the fund switch.

## Open questions (defaults used until answered)

1. Do analysts have Google accounts the fund can use? Default: Google, GitHub as fallback.
2. Desks and their leads. Default: an admin enters them.
3. Do PMs see pitches only after a desk lead approves? Default: PMs see everything, filtered by status.
4. Can Sinn and Rev people see each other's pitches and positions? Default: no; PMs and admins see both.
5. Must a trade have an approved pitch before it is placed? Default: no — links are a record, not a gate (a gate needs ADR-0025's trade staging).

## Consequences

- One new service, Postgres, oauth2-proxy and a nightly backup to run.
- Analysts and desk leads need accounts; access is revoked by removing them from `people`.
- The trade log becomes visible only to signed-in people, matching ADR-0005's note that positions may be sensitive.
- The dashboards and their APIs do not change.

Links: ADR-0005, ADR-0008, ADR-0025, ADR-0032, `services/flex-ingest/README.md`, `services/flex-web-live/README.md`.
