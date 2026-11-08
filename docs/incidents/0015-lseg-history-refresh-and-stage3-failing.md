# INC-0015 — LSEG history refresh failing; Stage 3 VaR stuck at 2026-10-22

- **Event period:** Stage 3 last succeeded 2026-10-24 (report date 2026-10-22); failed 2026-10-30 19:41 UTC; history refresh failed 2026-11-07 11:30–11:36 UTC
- **Reported / detected:** 2026-11-08 while planning the Risk management tab
- **Category:** Operations / data freshness
- **Status:** Mitigated 2026-11-08 — fix script run; history API live on 8092; Stage 1–4 retired by ADR-0029
- **Severity / audience impact:** Medium — no current VaR from the old pipeline; the server price cache cannot feed the new risk job
- **Evidence level:** systemd state, unit files, code reading; logs not readable by operator accounts
- **Owner:** Server admin (sudo) with dashboard maintainers

## Observations
- `lseg-history-refresh.service` exits 1 after about 6.5 minutes, consistent with looping through roughly 320 symbols. The job returns 1 if **any** symbol fails. `lseg-feed` streamed normally with the same login through 2026-11-06, so authentication is not the suspected cause.
- `lseg-history-api.service` was never installed. Its template points at `/opt/lseg-history/.venv`, while the venv is `/opt/lseg-history/venv` (as in INC-0005).
- `sinn-stage3-build.service` sets `STAGE1_MODULE=/opt/sinn-flex-stage1/flex_stage1.py`, which does not exist; the module is `/opt/sinn-risk-stage1/flex_risk1.py`. It loads the 2026-11-06 archive correctly when pointed at that path.
- `sinn-stage2-build.timer` and `sinn-stage3-build.timer` are disabled.
- Operator account `enoch` has no general sudo and cannot read `/var/lib/lseg-history` or the journal.

## Root cause
History refresh: **unconfirmed** (likely a few failing symbols). Stage 3: wrong module path plus timers that were never enabled (verified by unit inspection).

## Mitigation and resolution
- New risk job uses an operator-run LSEG backfill instead (ADR-0030).
- `deploy/risk-pipeline/fix-risk-pipeline.sh` (staged at `~enoch/fix-risk-pipeline.sh` on sinnfund) prints the refresh failure and failing symbols, installs `lseg-history-api` with the corrected venv path, and runs a refresh. Since 2026-11-08 it also adds the fund's five listed options to the delayed feed (step 5; backs up `lseg-symbols.env` first). Not yet run.

## Prevention / follow-ups
- [x] 2026-11-08: ran `sudo bash ~enoch/fix-risk-pipeline.sh`. Failing symbols: CCXI (no longer held) and TCBI.K (Texas Stock Exchange RIC, no daily history); 318 of 320 refreshed.
- [x] The refresh now fails only when most symbols fail (script step 1b); per-symbol errors stay in `sync_state`.
- [ ] Grant operators `systemctl start/restart` and `journalctl -u` for `lseg-history-refresh` and `lseg-history-api`.
- [ ] Point `risk_job.py` at `127.0.0.1:8092` once the API is up.
- [x] Option RICs confirmed: all five return LSEG quotes. This needed a feed fix: with `LSEG_AUTO_RESOLVE_FLEX=1` the resolved map replaced `LSEG_SYMBOLS`, so it is now merged over it (`services/lseg-feed/feed_api.py`). Coverage went from 287 to 361 symbols.
- [ ] Remove expired options from `lseg-symbols.env` (IWM expires 2026-11-20).
- [ ] Map TCBI's history to a RIC with daily data, or drop it from the list.

## Links
ADR-0012, ADR-0029, ADR-0030, INC-0005.
