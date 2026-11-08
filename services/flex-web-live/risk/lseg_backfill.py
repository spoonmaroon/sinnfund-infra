# /// script
# requires-python = ">=3.12"
# dependencies = ["lseg-data==2.1.1"]
# ///
"""Backfill daily closes from your own LSEG login into a CSV for the risk job.

Stand-in until the server's lseg-history service is fixed. Runs on your machine
with your LSEG cloud login (same as ~/lseg-test/lseg-quickstart), read from
LSEG_APP_KEY / LSEG_USER / LSEG_PASSWORD or ~/.config/research-keys. Close
Workspace first if it uses the same login (one session per login).

    uv run --script lseg_backfill.py lseg-symbols.env prices.csv --report report.json
    scp prices.csv sinnfund:/opt/flex-web-live/risk/prices.csv

LSEG data is licensed: keep the CSV private (it is never served publicly).
"""
import argparse
import csv
import json
import os
import sys
from datetime import date, timedelta
from pathlib import Path

import lseg.data as ld

KEYS = Path("~/.config/research-keys").expanduser()
FIELD = "TRDPRC_1"
BATCH = 50


def secret(var: str, filename: str) -> str:
    value = os.environ.get(var) or ((KEYS / filename).read_text().strip() if (KEYS / filename).is_file() else "")
    if not value:
        raise SystemExit(f"missing {var}: set it or save it in {KEYS / filename}")
    return value


def mapping(path: Path) -> dict[str, str]:
    """symbol -> history RIC from the feed's LSEG_SYMBOLS=SYM=/RIC,... line."""
    line = next(l for l in path.read_text().splitlines() if l.strip().startswith("LSEG_SYMBOLS="))
    pairs = (term.split("=", 1) for term in line.split("=", 1)[1].strip().strip("\"'").split(",") if "=" in term)
    return {symbol.strip().upper(): ric.strip().removeprefix("/") for symbol, ric in pairs}


def extra_symbols(report: Path, known: dict[str, str]) -> dict[str, str]:
    """symbol -> ISIN for stocks held but missing from the feed's symbol map."""
    positions = json.loads(report.read_text())["positions"]
    return {p["symbol"].upper(): p["isin"] for p in positions
            if p.get("asset_category") == "STK" and p.get("isin") and p["symbol"].upper() not in known}


def isin_rics(isins: dict[str, str]) -> dict[str, str]:
    """symbol -> RIC via LSEG's own ISIN lookup (primary listing)."""
    found = {}
    by_isin = {isin: symbol for symbol, isin in isins.items()}
    frame = ld.discovery.convert_symbols(symbols=list(by_isin), to_symbol_types="RIC")
    columns = [c for c in frame.columns if str(c).upper() == "RIC"] or \
              [c for c in frame.columns if "RIC" in str(c).upper()]
    if not columns:
        raise SystemExit(f"ISIN lookup returned no RIC column: {list(frame.columns)}")
    for isin, ric in frame[columns[0]].items():
        ric = str(ric) if isinstance(ric, str) and ric else None
        if ric and isin in by_isin:
            found[by_isin[isin]] = ric
    return found


def closes(frame, rics: list[str]) -> dict[str, dict[str, float]]:
    """{ric: {YYYY-MM-DD: close}} from get_history's single- or multi-RIC frame."""
    out: dict[str, dict[str, float]] = {}
    if frame is None or frame.empty:
        return out
    for column in frame.columns:
        parts = column if isinstance(column, tuple) else (column,)
        ric = next((p for p in parts if p in rics), rics[0] if len(rics) == 1 else None)
        if ric is None or (len(parts) > 1 and FIELD not in parts):
            continue
        for day, value in frame[column].items():
            if value == value and float(value) > 0:  # skip NaN and non-positive marks
                out.setdefault(ric, {})[day.date().isoformat()] = float(value)
    return out


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("symbols", type=Path, help="copy of /opt/lseg-feed/lseg-symbols.env")
    parser.add_argument("out", type=Path)
    parser.add_argument("--days", type=int, default=760, help="calendar days back (760 ≈ 2 years)")
    parser.add_argument("--report", type=Path, help="saved /api/v1/report: also fetch held stocks missing from the map")
    args = parser.parse_args()

    symbols = mapping(args.symbols)
    by_ric = {ric: symbol for symbol, ric in symbols.items()}
    end = date.today() - timedelta(days=1)
    start = end - timedelta(days=args.days)

    session = ld.session.platform.Definition(
        app_key=secret("LSEG_APP_KEY", "lseg-app-key"),
        grant=ld.session.platform.GrantPassword(secret("LSEG_USER", "lseg-user"),
                                                secret("LSEG_PASSWORD", "lseg-password")),
    ).get_session()
    session.open()
    if str(session.open_state) != "OpenState.Opened":
        raise SystemExit("LSEG session did not open. If Workspace is running with this login, "
                         "close it (one session per login) and run again.")
    ld.session.set_default(session)
    rows, missing = [], []
    try:
        if args.report:
            wanted = extra_symbols(args.report, symbols)
            resolved = isin_rics(wanted)
            for symbol in sorted(set(wanted) - set(resolved)):
                # The ISIN lookup skips funds; US ETFs use the bare ticker or an exchange suffix.
                for ric in (symbol, f"{symbol}.K", f"{symbol}.P", f"{symbol}.O"):
                    try:
                        if closes(ld.get_history(universe=ric, fields=[FIELD], interval="daily",
                                                 start=end.isoformat(), end=end.isoformat()), [ric]) or \
                           closes(ld.get_history(universe=ric, fields=[FIELD], interval="daily",
                                                 start=(end - timedelta(days=10)).isoformat(),
                                                 end=end.isoformat()), [ric]):
                            resolved[symbol] = ric
                            break
                    except Exception:
                        continue
            print(f"ISIN lookup: {len(resolved)}/{len(wanted)} extra stocks resolved; "
                  f"unresolved: {', '.join(sorted(set(wanted) - set(resolved))) or 'none'}", file=sys.stderr)
            symbols.update(resolved)
            by_ric.update({ric: symbol for symbol, ric in resolved.items()})
        rics = list(by_ric)
        for i in range(0, len(rics), BATCH):
            batch = rics[i:i + BATCH]
            try:
                got = closes(ld.get_history(universe=batch, fields=[FIELD], interval="daily",
                                            start=start.isoformat(), end=end.isoformat()), batch)
            except Exception as exc:  # one bad RIC can fail a batch: retry one by one
                print(f"batch {i // BATCH + 1} failed ({type(exc).__name__}); retrying singly", file=sys.stderr)
                got = {}
                for ric in batch:
                    try:
                        got.update(closes(ld.get_history(universe=ric, fields=[FIELD], interval="daily",
                                                         start=start.isoformat(), end=end.isoformat()), [ric]))
                    except Exception as single:
                        print(f"  {ric}: {type(single).__name__}", file=sys.stderr)
            for ric in batch:
                if not got.get(ric):
                    missing.append(ric)
                for day, close in sorted(got.get(ric, {}).items()):
                    rows.append((day, by_ric[ric], ric, close))
            print(f"{min(i + BATCH, len(rics))}/{len(rics)} RICs", file=sys.stderr)
    finally:
        session.close()

    tmp = args.out.with_suffix(".tmp")
    with tmp.open("w", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["date", "symbol", "ric", "close"])
        writer.writerows(sorted(rows))
    tmp.replace(args.out)
    print(f"wrote {len(rows)} closes for {len(symbols) - len(missing)}/{len(symbols)} symbols "
          f"({start} to {end}); no data: {', '.join(missing) or 'none'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
