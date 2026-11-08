#!/usr/bin/env bash
# Get the server's LSEG daily price history working for the dashboard's risk tab.
# Run once as an admin:   sudo bash fix-risk-pipeline.sh
#
# The risk tab (flex-web-live) reads prices from lseg-history-api on 127.0.0.1:8092.
# The old sinn-risk stage 1-4 VaR pipeline is retired and left untouched.
#
# What was broken (found 2026-11-08):
#   1. lseg-history-refresh exits 1 if any single symbol fails; step 1 shows which ones and why,
#      step 1b makes it fail only when most symbols fail.
#   2. lseg-history-api was never installed, and its unit file points at /opt/lseg-history/.venv,
#      but the venv is /opt/lseg-history/venv (see docs/incidents/0005).
#   3. The delayed LSEG feed (lseg-feed, 8091) only carries stocks and ETFs, so the fund's
#      listed options show statement prices only. Step 5 adds them (added 2026-11-08).
set -euo pipefail

[[ $EUID -eq 0 ]] || { echo "Run with sudo." >&2; exit 1; }
DB=/var/lib/lseg-history/prices.sqlite3

echo "== 1. Why lseg-history-refresh failed (last run)"
journalctl -u lseg-history-refresh -n 40 --no-pager || true
if [[ -f $DB ]]; then
  # The sqlite3 CLI is not installed on this host; Python's sqlite3 module is.
  sudo -u svc-lseg python3 - "$DB" <<'EOF'
import sqlite3, sys
db = sqlite3.connect(f"file:{sys.argv[1]}?mode=ro", uri=True)
print("sync_state:", db.execute("select status, count(*) from sync_state group by status").fetchall())
print("latest price day / symbols:", db.execute("select max(date), count(distinct symbol) from daily_prices").fetchone())
for row in db.execute("select symbol, ric, message from sync_state where status='error' order by symbol limit 40"):
    print("  failed:", *row)
EOF
else
  echo "($DB missing: the history refresh has never written prices)"
fi

echo "== 1b. Count the refresh as failed only when most symbols fail"
# 2026-11-07: 318 ok, 2 failed (CCXI no longer held; TCBI.K is a Texas Stock Exchange RIC with
# no daily history) and the whole run exited 1. Failed symbols stay listed in sync_state.
HIST=/opt/lseg-history/history_api.py
if grep -q '    return 1 if failed else 0' "$HIST"; then
  cp -a "$HIST" "$HIST.bak-$(date -u +%Y%m%dT%H%M%SZ)"
  sed -i 's/    return 1 if failed else 0/    return 1 if failed > succeeded else 0/' "$HIST"
fi

echo "== 2. Install the read-only history API on 127.0.0.1:8092 (never proxied by nginx)"
sed 's#/opt/lseg-history/.venv/#/opt/lseg-history/venv/#' \
  /opt/lseg-history/deploy/lseg-history-api.service > /etc/systemd/system/lseg-history-api.service
/opt/lseg-history/venv/bin/python -c "import uvicorn" \
  || /opt/lseg-history/venv/bin/pip install -q uvicorn
systemctl daemon-reload
systemctl enable --now lseg-history-api.service

echo "== 3. Refresh prices now (~7 minutes; the timer then runs Tue-Sat 06:30 ET)"
# A non-zero exit usually means a few symbols failed, not that the run was useless.
systemctl start lseg-history-refresh.service || echo "lseg-history-refresh reported failures; see step 1"

echo "== 4. Result"
sleep 2
curl -s http://127.0.0.1:8092/health; echo
curl -s 'http://127.0.0.1:8092/history/AVGO?limit=3'; echo

echo "== 5. Option prices: add the fund's listed options to the delayed LSEG feed (8091)"
# Key = OCC symbol without spaces (what flex-web-live looks up); value = delayed OPRA RIC:
# root + month code (calls A-L, puts M-X) + day + yy + strike x100 (5 digits) + .U.
# BRK.B: IBKR's "BRK B" (keys cannot hold a space; flex-web-live looks it up with a dot).
# Foreign listings the Flex resolver skips (verified 2026-11-08). Not BABl: LSE quotes are in
# pence while IBKR reports pounds, so the price-ratio check would reject it anyway.
# shortcut: fixed list of the 5 options held on 2026-11-06; IWM expires 2026-11-20. Remove expired
# entries by hand until the feed's Flex resolver (LSEG_AUTO_RESOLVE_FLEX) learns options.
FEED_ENV=/opt/lseg-feed/lseg-symbols.env
OPTIONS="BRK.B=/BRKb,VWSC=/VWS.CO,WCP=/WCP.TO,ATZ=/ATZ.TO,ARA=/ARA.TO,D05=/DBSM.SI,IWM261120P00280000=/IWMW202628000.U,GLD270115P00370000=/GLDM152737000.U,GLD270115C00410000=/GLDA152741000.U,CCL270115P00020000=/CCLM152702000.U,TLT270416C00080000=/TLTD162708000.U"
KEYS=$(tr ',' '\n' <<<"$OPTIONS" | cut -d= -f1 | paste -sd,)
if grep -q BRK.B= "$FEED_ENV"; then
  echo "(options already in $FEED_ENV)"
else
  cp -a "$FEED_ENV" "$FEED_ENV.bak-$(date -u +%Y%m%dT%H%M%SZ)"   # restore this file to undo
  sed -i "1s#\$#,$OPTIONS#" "$FEED_ENV"
fi
systemctl restart lseg-feed.service
sleep 20
# Off-hours: one snapshot now. Market hours: the stream picks them up and this says so.
curl -s -X POST http://127.0.0.1:8091/refresh; echo
sleep 60
echo "Option quotes (an entry under errors means LSEG rejected that RIC; send it to the dashboard team):"
curl -s "http://127.0.0.1:8091/prices?symbols=$KEYS" | python3 -m json.tool | grep -E '"(price|ric|errors)"|[A-Z]{3}2[67][0-9]{4}[CP]' || true
