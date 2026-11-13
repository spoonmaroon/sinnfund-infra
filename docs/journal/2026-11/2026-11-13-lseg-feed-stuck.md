# 2026-11-13 — LSEG feed stuck since Nov 12; the login's one session is held elsewhere

## What happened

- The delayed price feed (`lseg-feed`) lost its websocket at 14:30 UTC on Nov 12, reconnected, and then sat in `connecting` for over a day, still refreshing its token. `/health` returned 503 and the newest quotes were from Nov 11, 21:19 UTC. The dashboard kept showing those as LSEG prices.
- The daily history refresh failed on Nov 13 at 11:30 UTC: `LSEG Platform session did not open`.
- Restarting `lseg-feed` at 21:23 UTC on Nov 13 brought it back to `market_closed` (healthy; it streams again at Monday's open).
- After the restart, a one-off session with `signon_control=False` was refused for at least 6 minutes with "Session quota is reached". The fund's LSEG login allows one session at a time, and something outside these services is holding it. The Mac's research terminal uses a different LSEG login, and the server's risk services do not use LSEG.

## Open

- Find who or what else is signed in with the fund's LSEG login. The feed signs in with `signon_control=True` and so displaces that session at each open, which may be what dropped the feed's websocket on Nov 12.
- The feed's worker has no timeout around `session.open()` / `stream.open()`, so one hang leaves it in `connecting` indefinitely. It needs a watchdog that restarts the attempt.
- The dashboard should label LSEG prices stale when `/health` is not ok.
