#!/usr/bin/env bash
# Get the server's LSEG daily price history working for the dashboard's risk tab.
# Run once as an admin:   sudo bash fix-risk-pipeline.sh
#
# The risk tab (flex-web-live) reads prices from lseg-history-api on 127.0.0.1:8092.
# The old sinn-risk stage 1-4 VaR pipeline is retired and left untouched.
#
# What was broken (found 2026-11-08):
#   1. lseg-history-refresh exits 1 if any single symbol fails; step 1 shows which ones and why.
#   2. lseg-history-api was never installed, and its unit file points at /opt/lseg-history/.venv,
#      but the venv is /opt/lseg-history/venv (see docs/incidents/0005).
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
