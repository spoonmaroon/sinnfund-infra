#!/usr/bin/env python3
"""Write the S&P 500 constituents as extra symbols for the LSEG history refresh.

The What if page can only model symbols with daily history; the refresh otherwise covers
the fund's holdings only. Output: /opt/lseg-history/universe.env with
    LSEG_HISTORY_EXTRA=AAPL=AAPL.OQ,BRK.B=BRKb.N,...
which lseg-history-refresh reads next to LSEG_SYMBOLS (holdings win on overlap).

Run as root with the lseg-history venv and the feed's LSEG login (add-sp500-history.sh step 2 does this):
    set -a; . /opt/lseg-feed/.env; set +a
    /opt/lseg-history/venv/bin/python sp500_universe.py /opt/lseg-history/universe.env
"""
import os
import re
import sys
import tempfile
from pathlib import Path

CHAIN = "0#.SPX"


def ticker(ric: str) -> str:
    """AAPL.OQ -> AAPL; BRKb.N -> BRK.B (LSEG writes the share class in lower case)."""
    root = ric.split(".")[0]
    match = re.fullmatch(r"([A-Z0-9]+)([a-z])", root)
    return f"{match[1]}.{match[2].upper()}" if match else root


def main() -> int:
    import lseg.data as ld

    session = ld.session.platform.Definition(
        app_key=os.environ["LSEG_APP_KEY"],
        grant=ld.session.platform.GrantPassword(
            username=os.environ["LSEG_USER_ID"], password=os.environ["LSEG_PASSWORD"]),
        signon_control=False,  # never displace the live feed's session
    ).get_session()
    session.open()
    if str(session.open_state) != "OpenState.Opened":
        raise SystemExit("LSEG session did not open (is another session using this login?)")
    try:
        ld.session.set_default(session)
        rics = sorted(ld.discovery.Chain(CHAIN).constituents)
    finally:
        session.close()
    if len(rics) < 450:
        raise SystemExit(f"Only {len(rics)} constituents from {CHAIN}; refusing to write a short list")
    entries = {ticker(ric): ric for ric in rics}
    out = Path(sys.argv[1])
    line = "LSEG_HISTORY_EXTRA=" + ",".join(f"{s}={r}" for s, r in sorted(entries.items())) + "\n"
    with tempfile.NamedTemporaryFile("w", dir=out.parent, delete=False) as fh:
        fh.write(f"# S&P 500 ({CHAIN}), {len(entries)} names; written by sp500_universe.py\n{line}")
    os.chmod(fh.name, 0o644)
    os.replace(fh.name, out)
    print(f"{len(entries)} S&P 500 symbols written to {out}")
    return 0


if __name__ == "__main__":
    assert ticker("AAPL.OQ") == "AAPL" and ticker("BRKb.N") == "BRK.B" and ticker("BFb.N") == "BF.B"
    sys.exit(main())
