#!/usr/bin/env python3
"""List every sender's latest signal state directly from the server ledger (read-only)."""
import argparse
import csv
import os
from pathlib import Path

from signal_client import print_table


def read_signals(path, destination=None, status=None):
    # The service atomically replaces this file, so an open reader sees one complete snapshot.
    with path.open(newline="") as stream:
        reader = csv.DictReader(stream)
        required = {"signal_id", "owner", "destination", "status", "revision"}
        if not required.issubset(reader.fieldnames or []):
            raise ValueError("Not a signal ledger")
        latest = {row["signal_id"]: row for row in reader}
    return [row for row in latest.values()
            if (not destination or row["destination"] == destination)
            and (not status or row["status"] == status)]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ledger", type=Path, default=Path(os.getenv(
        "SIGNAL_DATA_DIR", "/opt/prediction-api/signal-data")) / "ledger.csv")
    parser.add_argument("--destination", choices=("LIVE", "PAPER"))
    parser.add_argument("--status", default="pending", help="Default pending; use all for all statuses")
    args = parser.parse_args(argv)
    try:
        print_table(read_signals(args.ledger, args.destination, None if args.status == "all" else args.status))
    except (OSError, ValueError) as error:
        raise SystemExit(f"Cannot read ledger: {error}") from None


if __name__ == "__main__":
    main()
