#!/usr/bin/env python3
"""One-off: fetch what year-to-date P&L needs from before the daily archives began.

Saves two raw Flex statements (same token and query as flex-ingest):
  <out>/start.xml.gz  positions and cash at the last day of the previous year
  <out>/flows.xml.gz  one range statement from Jan 1 to the day before the first daily
                      archive, whose CashReport gives deposits, withdrawals and transfers
flex-web-live adds the flows in the daily archives from then on.

Needs flex-ingest's is_partial(), which is on the unmerged ingest branch (claude/serene-tesla-3decfb)
and already deployed on the server; with main's flex-ingest it stops after one Flex request.

Run as the ingest user with its venv (it reads /opt/flex-ingest/.env):
    sudo -u svc-flex /opt/flex-ingest/venv/bin/python pull_year_start.py 2026 20261008
"""
import datetime as dt
import gzip
import importlib.util
import os
import sys
import time
from pathlib import Path

spec = importlib.util.spec_from_file_location("flex_ingest", "/opt/flex-ingest/flex-ingest.py")
ingest = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ingest)


def pull(token: str, query_id: str, start: str, end: str) -> bytes:
    """flex-ingest's pull() for a date range instead of one day."""
    response = ingest.request("SendRequest", {"t": token, "q": query_id, "v": 3, "fd": start, "td": end})
    status, code, message, reference = ingest.api_status(response)
    if status != "Success" or not reference:
        raise ingest.FlexError(f"SendRequest {start}-{end}: {code or 'unknown'}: {message or 'failed'}")
    deadline = time.monotonic() + ingest.TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        time.sleep(ingest.POLL_SECONDS)
        response = ingest.request("GetStatement", {"t": token, "q": reference, "v": 3})
        if b"<FlexQueryResponse" in response[:500]:
            return response
        status, code, message, _ = ingest.api_status(response)
        if code not in ingest.RETRY_LATER | {ingest.STILL_BUILDING}:
            raise ingest.FlexError(f"GetStatement: {code or status}: {message or 'failed'}")
    raise ingest.FlexError("GetStatement: report not ready before timeout")


def main() -> int:
    year, last_before_archives = sys.argv[1], sys.argv[2]
    ingest.load_dotenv(ingest.ENV_FILE, override=False)
    (name, token, query_id), *_ = ingest.books()
    # Last trading day of the previous year: step back from Dec 31 over a weekend.
    day = dt.date(int(year) - 1, 12, 31)
    while day.weekday() >= 5:
        day -= dt.timedelta(days=1)
    out = ingest.DATA_DIR.parent / "ytd" / year
    out.mkdir(parents=True, exist_ok=True)
    for label, start, end in (("start", day.strftime("%Y%m%d"), day.strftime("%Y%m%d")),
                              ("flows", f"{year}0101", last_before_archives)):
        xml = pull(token, query_id, start, end)
        if ingest.is_partial(xml):
            raise SystemExit(f"{label} {start}-{end}: IBKR excluded accounts; try again later")
        (out / f"{label}.xml.gz").write_bytes(gzip.compress(xml))
        print(f"{label}: {start}-{end}, {len(xml)} bytes, {xml.count(b'<FlexStatement ')} statements")
        time.sleep(ingest.POLL_SECONDS)  # IBKR throttles back-to-back requests
    return 0


if __name__ == "__main__":
    sys.exit(main())
