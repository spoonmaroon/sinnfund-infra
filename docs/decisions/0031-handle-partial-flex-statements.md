# ADR-0031: Retry partial Flex statements at ingest and carry excluded accounts forward when reading

- **Date:** 2026-11-08
- **Status:** Accepted — deployed (ingest fix from commit `4a647a9`, dashboard carry-forward)

## Context

IBKR returned the 2026-11-06 statement with three accounts excluded, including the largest. The ingest job archived it as complete and never retried it, so the dashboard showed about $1.9M of positions instead of about $4.6M (INC-0013).

## Options considered

- Overwrite archives with `--force` by hand when noticed.
- Detect partial statements at ingest and retry the day automatically.
- Also make readers robust to an incomplete latest file.

## Decision

**Ingest:** a statement containing "accounts were excluded from this statement" is saved as `<book>.xml.gz.partial`. A day holding only a partial is not "archived", and each timer run retries the last week's partial days. A partial never replaces a complete archive, and an older partial is kept as evidence (ADR-0007).

**Read side:** if an account has no `FlexStatement` in the latest file, `flex-web-live` carries its positions and cash forward from the previous file, reprices them with delayed quotes, and names the accounts in the UI. The history series carries them forward with zero P&L for that day.

## Rationale

The ingest fix repairs the source data; the read-side guard keeps totals correct until it does, and makes the gap visible instead of silent.

## Consequences and limitations

Carried rows reflect yesterday's quantities; trades in those accounts on the missing day are not visible until the full statement arrives.

## Follow-up / revisit criteria

Confirm that the next timer run (2026-11-10 11:00 UTC) replaces the 2026-11-06 partial with a complete statement. Merge the ingest fix branch into `main`.

Links: INC-0013, ADR-0007, `services/flex-ingest/README.md`.
