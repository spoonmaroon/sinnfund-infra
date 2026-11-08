# INC-0014 — Dashboard summed non-USD positions as if they were dollars; day change read $0

- **Event period:** Since the merged dashboard went live (about 2026-11-07); found 2026-11-08
- **Reported / detected:** Column audit requested by an operator
- **Category:** Correctness
- **Status:** Resolved 2026-11-08
- **Severity / audience impact:** High — totals overstated by roughly $600k; day change misleading on every non-trading day
- **Evidence level:** Measured against archive values
- **Owner:** Portfolio dashboard maintainers

## Expected versus observed behavior
1. **FX.** Flex reports `positionValue`, `costBasisMoney` and `fifoPnlUnrealized` in the position's currency, with `fxRateToBase` beside them. The dashboard added them as USD: a yen holding worth ¥608,000 (about $3,837) counted as $608,000. DKK, CAD and EUR rows were also affected.
2. **Day change.** "Price chg" and "Day chg" compared the delayed quote with the same day's close. On weekends both are the same session, so every row showed $0, and close-only foreign listings showed "—".
3. **"Close" badge.** It used the CSS class `close`, which already styled the account dialog's 18px close button, so it rendered oversized.

## Root cause
The parser never read `fxRateToBase`; day change had no previous close to compare with; a generic class name collided.

## Mitigation and resolution
Parse `fx_rate_to_base` and convert every total, account and sector sum to USD while showing prices in local currency. Compute day change against the previous statement's close when the quote is from the statement's session (details in `services/flex-web-live/README.md`). Rename the badge class. Tests: `tests/test_day_change.py` and `tests/dashboard.test.mjs`.

## Prevention / follow-ups
- [x] Unit tests for FX conversion, weekend day change, close-only rows and new positions.
- [ ] Model FX moves in risk (ADR-0029 follow-up).
- [x] 2026-11-08 regression: an off-hours manual LSEG refresh stamped quotes on Sunday, which read as a new session and zeroed day change for every Nov 6 position. Weekend and pre-open timestamps now count as the last session (`session_date()`, test in `tests/test_day_change.py`).

## Links
ADR-0027, `services/flex-web-live/portfolio_api.py`.
