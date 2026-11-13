#!/usr/bin/env bash
# Add S&P 500 daily price history so the What if page can model names the fund doesn't hold.
# Run once as an admin:   sudo bash add-sp500-history.sh
#
# The LSEG history refresh normally covers LSEG_SYMBOLS (the fund's holdings, shared with the
# live feed). This gives the refresh its own extra list, LSEG_HISTORY_EXTRA in
# /opt/lseg-history/universe.env, so the live feed does not stream 500 more quotes.
# Holdings win when a ticker is in both. Re-run to refresh the constituents list.
set -euo pipefail
[[ $EUID -eq 0 ]] || { echo "Run with sudo." >&2; exit 1; }
HERE=$(cd "$(dirname "$0")" && pwd)
HIST=/opt/lseg-history/history_api.py
UNIVERSE=/opt/lseg-history/universe.env

echo "== 1. Let the history refresh read LSEG_HISTORY_EXTRA"
# One backup of the untouched file: two patches in the same second would otherwise share a name.
if ! grep -q LSEG_HISTORY_EXTRA "$HIST" || ! grep -q "from contextlib import closing" "$HIST"; then
  cp -a "$HIST" "$HIST.bak-$(date -u +%Y%m%dT%H%M%SZ)"
fi
if ! grep -q LSEG_HISTORY_EXTRA "$HIST"; then
  python3 - "$HIST" <<'EOF'
import sys
path = sys.argv[1]
text = open(path).read()
old_spec = '    spec = os.environ.get("LSEG_SYMBOLS", "").strip()\n'
new_spec = ('    spec = ",".join(filter(None, (os.environ.get("LSEG_SYMBOLS", "").strip(),\n'
            '                                  os.environ.get("LSEG_HISTORY_EXTRA", "").strip())))\n')
old_dup = '            raise ValueError(f"Duplicate ticker: {symbol}")\n'
new_dup = '            continue  # holdings (LSEG_SYMBOLS, first) win over LSEG_HISTORY_EXTRA\n'
assert text.count(old_spec) == 1 and text.count(old_dup) == 1, "history_api.py changed; patch by hand"
open(path, "w").write(text.replace(old_spec, new_spec).replace(old_dup, new_dup))
EOF
fi
# history_api.py opened a SQLite connection per request and never closed it ("with
# connect()" only commits), so ~1,000 requests exhausted file handles (Errno 24).
if ! grep -q "from contextlib import closing" "$HIST"; then
  python3 - "$HIST" <<'EOF'
import sys
path = sys.argv[1]
text = open(path).read()
assert "with connect() as db:" in text and "import argparse\n" in text, "history_api.py changed; patch by hand"
text = text.replace("import argparse\n", "import argparse\nfrom contextlib import closing\n", 1)
text = text.replace("with connect() as db:", "with closing(connect()) as db:").replace("with connect():", "with closing(connect()):")
open(path, "w").write(text)
EOF
  systemctl restart lseg-history-api.service
fi
mkdir -p /etc/systemd/system/lseg-history-refresh.service.d
printf '[Service]\nEnvironmentFile=-%s\n' "$UNIVERSE" > /etc/systemd/system/lseg-history-refresh.service.d/universe.conf
systemctl daemon-reload

echo "== 2. Write the S&P 500 constituents (LSEG chain 0#.SPX)"
# A private folder: lseg.data reads lseg-data.config.json from the working folder and HOME, and a
# planted one in /tmp could point this root login at someone else's server.
WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT
( set -a; . /opt/lseg-feed/.env; set +a
  cd "$WORK" && HOME="$WORK" /opt/lseg-history/venv/bin/python "$HERE/sp500_universe.py" "$UNIVERSE" )

echo "== 3. Fetch ~3 years of daily closes for the new names (about 15 minutes)"
systemctl start lseg-history-refresh.service || echo "refresh reported failures; see journalctl -u lseg-history-refresh"
curl -s http://127.0.0.1:8092/health; echo
