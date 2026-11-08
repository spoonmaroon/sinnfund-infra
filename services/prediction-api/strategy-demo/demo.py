#!/usr/bin/env python3
"""Prepare a sample signal, then explicitly create, inspect, edit or delete it."""

import argparse
import json
from pathlib import Path
import sys
import time
import uuid

from strategy import target_price

# Reuse the real client's authentication, validation, HTTPS and revision handling.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import signal_client as client


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("save-key", "prepare", "create", "get", "edit", "delete", "list"))
    parser.add_argument("--file", type=Path, default=Path(__file__).with_name("signal.json"))
    parser.add_argument("--price", type=client.positive_price, help="New target price for edit")
    parser.add_argument("--send", action="store_true", help="Perform create/edit/delete over HTTPS")
    args = parser.parse_args(argv)

    if args.action in ("save-key", "list"):
        return client.main([args.action])
    if args.action == "prepare":
        signal = {
            "signal_id": "demo-" + uuid.uuid4().hex,
            "destination": "LIVE",
            "ticker": "DEMO",
            "price": target_price([98, 100, 102, 104, 96]),
            "timestamp": int(time.time()),
        }
        # Never overwrite a prior request: retries need its original ID AND timestamp.
        try:
            with args.file.open("x") as output:
                json.dump(signal, output, indent=2)
                output.write("\n")
        except FileExistsError:
            raise SystemExit("File already exists; reuse it, or choose a new --file") from None
        print(f"Prepared {args.file}; no network request made.")
        print(json.dumps(signal, indent=2))
        return 0

    try:
        signal = json.loads(args.file.read_text())
        signal_id = client.matching(client.SIGNAL_ID, "signal ID")(signal["signal_id"])
        if args.action == "create":
            command = ["create", signal_id, signal["destination"], signal["ticker"],
                       str(signal["price"]), str(signal["timestamp"])]
            if signal["destination"] != "LIVE":
                parser.error("This demo uses LIVE ledger records, not PAPER orders")
        elif args.action == "edit":
            if args.price is None:
                parser.error("edit requires --price")
            command = ["edit", signal_id, "--price", str(args.price)]
        else:
            command = [args.action, signal_id]
        client.parser().parse_args(command)
    except (OSError, ValueError, KeyError, TypeError, argparse.ArgumentTypeError) as error:
        raise SystemExit(f"Cannot use signal file: {error}") from None

    if args.action != "get" and not args.send:
        print("Preview only: signal_client.py " + " ".join(command))
        print("Add --send to perform this operation.")
        return 0
    # get is read-only. edit/delete fetch the latest revision before changing it.
    return client.main(command)


if __name__ == "__main__":
    raise SystemExit(main())
