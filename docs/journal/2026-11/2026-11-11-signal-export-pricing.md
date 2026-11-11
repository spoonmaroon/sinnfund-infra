# 2026-11-11 — Signal export could not price a real batch

## What happened

The first real batch of LIVE signals (82 US large caps from one sender, Nov 9) used LSEG RICs as tickers (`ZBH.N`, `ZBRA.OQ`). Checking them before any export found two problems that would each have refused the whole export:

1. **Ticker format.** Export quotes come from Twelve Data, which rejects `ZBH.N` but prices `ZBH`. One missing quote fails the whole export.
2. **Rate limit.** The Twelve Data plan allows 8 symbols per minute (800 a day). An export quotes every pending symbol in one request, and the quotes must be fetched within 30 seconds, so any export of more than 8 symbols is refused.

Fifteen of the names were also missing from the LSEG history service (it covers holdings and the S&P 500); an ad-hoc LSEG pull priced all of them.

## Fixed

- `signal_ledger.us_ticker()` turns LSEG US RICs (`.N`, `.OQ`, `.O`) into plain tickers for quotes, the duplicate check and the targets CSV. The ledger keeps what the sender wrote, so pending signals work without editing the ledger. Deployed to `/opt/prediction-api` (backup under `deploy-backups/us-ticker-20261111T193850Z/`).

## Open

- The 8-symbols-per-minute limit still blocks a large export. Options: a paid Twelve Data plan, or quote exports from LSEG (delayed RIC snapshot, already licensed for the fund's own use). Owner decision.
- Price history for signal tickers outside the S&P 500 list, so batches can be checked without an ad-hoc pull.
