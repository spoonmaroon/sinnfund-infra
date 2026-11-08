#!/bin/sh
# Run manually or from cron. Default: prepare and preview, without sending.
set -eu
DEMO_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
PYTHON=${PYTHON:-python3}
# Explicitly use the documented draft/test service. Override for your test deployment.
export SIGNAL_API_BASE_URL=${SIGNAL_API_BASE_URL:-https://sinnfund.jkoh.dev/prediction}
case "${1-}" in
  ""|--send) ;;
  *) echo "Usage: sh run_daily.sh [--send]" >&2; exit 2 ;;
esac
mkdir -p "$DEMO_DIR/runs"
# One saved request per UTC day; retries reuse the exact payload, including timestamp.
SIGNAL_FILE="$DEMO_DIR/runs/$(date -u +%F).json"
if [ ! -f "$SIGNAL_FILE" ]; then
  "$PYTHON" "$DEMO_DIR/demo.py" prepare --file "$SIGNAL_FILE"
fi
# shortcut: static teaching prices, replace with fresh validated data before real scheduling.
if [ "${1-}" = --send ]; then
  "$PYTHON" "$DEMO_DIR/demo.py" create --file "$SIGNAL_FILE" --send
else
  "$PYTHON" "$DEMO_DIR/demo.py" create --file "$SIGNAL_FILE"
fi
