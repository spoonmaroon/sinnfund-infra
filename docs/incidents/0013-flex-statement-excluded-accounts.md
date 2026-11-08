# INC-0013 — IBKR Flex statement for 2026-11-06 excluded three accounts

- **Event period:** Statement generated 2026-11-06 23:15 (IBKR `whenGenerated`); found 2026-11-08
- **Reported / detected:** Dashboard audit; position count fell from 403 to 310
- **Category:** Correctness / source-data completeness
- **Status:** Mitigated (dashboard carry-forward); resolution pending the 2026-11-10 ingest retry
- **Severity / audience impact:** High — headline value showed about $1.87M instead of about $4.6M; risk would have omitted the largest account
- **Evidence level:** Raw archive and logs
- **Owner:** Portfolio dashboard maintainers

## Expected versus observed behavior
Each daily archive should contain a statement for every account. The 2026-11-06 file has 22 `FlexStatement` elements and the message "The following accounts were excluded from this statement" naming three accounts (Tier One, Metals & Materials, Derivatives). The 2026-11-07 timer run found `main.xml.gz` present and skipped the day, so the gap was never retried.

## Impact and scope
`/live/` and `/close/` undercounted positions, P&L and accounts from 2026-11-07 until the mitigation. The history series also showed false value drops on 2026-10-21 and 2026-11-06, the two days with fewer accounts.

## Root cause
`flex-ingest.py` treated any `FlexQueryResponse` as complete and skipped days with an existing archive. IBKR's reason for excluding the accounts (likely statements not yet ready at 23:15) is **unconfirmed**.

## Mitigation and resolution
- 2026-11-08: `flex-web-live` carries accounts with no statement forward from the previous file, reprices them, and names them in the UI (ADR-0031). Totals: $4,636,034 of positions across 20 accounts.
- 2026-11-08: deployed the ingest fix (commit `4a647a9`; backup in `/opt/flex-ingest/deploy-backups/partial-retry-*`): partial statements are saved as `.xml.gz.partial` and recent partial days are retried each run. A run was **not** triggered on Sunday, because it would have requested Saturday.

## Prevention / follow-ups
- [ ] Confirm the 2026-11-10 11:00 UTC run replaces the 2026-11-06 partial with a complete statement.
- [ ] Merge branch `claude/serene-tesla-3decfb` (ingest fix) into `main`.

## Links
ADR-0031, ADR-0007, `services/flex-ingest/README.md`, `services/flex-web-live/README.md`.
