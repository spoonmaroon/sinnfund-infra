#!/usr/bin/env python3
"""Bounded sustained create/delete test. Leaves deleted audit history; never exports."""
import argparse
from concurrent.futures import ThreadPoolExecutor, wait
import json
from pathlib import Path
import secrets
import threading
import time

from signal_client import api_request, credential


def owned_test_records(records, prefix):
    return [r for r in records if r["signal"]["signal_id"].startswith(prefix)
            and (r["status"] == "pending" or
                 r["status"] == "rejected" and r.get("reason") == "no_direction")]


def cleanup(token, prefix):
    for _ in range(3):
        records = owned_test_records(api_request("GET", "/signals", token), prefix)
        if not records:
            return
        for offset in range(0, len(records), 100):
            api_request("POST", "/signals/delete", token, [
                {"signal_id": r["signal"]["signal_id"], "revision": r["revision"]}
                for r in records[offset:offset+100]])
    raise RuntimeError("Cleanup could not be verified; inspect this run's prefix")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seconds", type=int, default=60)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--batch-size", type=int, default=100)
    parser.add_argument("--output", type=Path, default=Path("signal-stress-results.json"))
    args = parser.parse_args(argv)
    if not (1 <= args.seconds <= 300 and 1 <= args.workers <= 10 and 1 <= args.batch_size <= 100):
        parser.error("seconds must be 1-300, workers 1-10, batch-size 1-100")
    token = credential()
    api_request("GET", "/signals", token)
    prefix = "sustain-" + secrets.token_hex(6) + "-"
    report = {"prefix": prefix, "requested_seconds": args.seconds, "workers": args.workers,
              "batch_size": args.batch_size, "requests": [], "errors": [], "cleanup_complete": False}
    stop = threading.Event()
    lock = threading.Lock()
    started = time.monotonic()
    deadline = started + args.seconds

    def call(path, body):
        began = time.monotonic()
        response = api_request("POST", path, token, body)
        elapsed = time.monotonic() - began
        with lock:
            report["requests"].append({"operation": "delete" if path.endswith("delete") else "create",
                "signals": len(body), "seconds": round(elapsed, 4),
                "started_at_seconds": round(began-started, 4)})
        if elapsed > 5:
            stop.set()
            with lock:
                report["errors"].append("Stopped new cycles: response exceeded five seconds")
        return response

    def worker(index):
        iteration = 0
        try:
            while time.monotonic() < deadline and not stop.is_set():
                batch = [{"signal_id":f"{prefix}{index}-{iteration}-{i}", "destination":"LIVE",
                          "ticker":"LOADTEST", "price":100, "timestamp":int(time.time())}
                         for i in range(args.batch_size)]
                response = call("/signals", batch)
                if len(response) != len(batch) or any(r["operation"] != "created" for r in response):
                    raise RuntimeError("Unexpected create confirmation")
                response = call("/signals/delete", [{"signal_id":r["signal_id"], "revision":r["revision"]}
                                                    for r in response])
                if len(response) != len(batch) or any(r["status"] != "deleted" for r in response):
                    raise RuntimeError("Unexpected delete confirmation")
                iteration += 1
        except (Exception, SystemExit) as error:
            stop.set()
            with lock:
                report["errors"].append(str(error))

    print(f"Run {prefix}: {args.seconds}s, {args.workers} workers, {args.batch_size} signals/request", flush=True)
    try:
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            pending = {pool.submit(worker, i) for i in range(args.workers)}
            while pending:
                _, pending = wait(pending, timeout=10)
                with lock:
                    created = sum(r["signals"] for r in report["requests"] if r["operation"] == "create")
                    deleted = sum(r["signals"] for r in report["requests"] if r["operation"] == "delete")
                print(f"{time.monotonic()-started:.1f}s: created {created}, deleted {deleted}", flush=True)
        report["duration_seconds"] = round(time.monotonic()-started, 3)
    finally:
        stop.set()
        try:
            cleanup(token, prefix)
            report["cleanup_complete"] = True
        except (Exception, SystemExit) as error:
            report["errors"].append("Cleanup: " + str(error))
        args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(f"Report: {args.output}; cleanup complete: {report['cleanup_complete']}", flush=True)
    return 1 if report["errors"] or not report["cleanup_complete"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
