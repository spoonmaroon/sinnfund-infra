#!/usr/bin/env python3
"""Read-only portfolio API and dashboard backed by IBKR Flex archives."""

from __future__ import annotations

import gzip
import json
import logging
import os
import re
import threading
from pathlib import Path

import requests
from fastapi import FastAPI, Query, Request
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, Response
from fastapi.staticfiles import StaticFiles

from parse_flex_positions_live import DEFAULT_ARCHIVE_DIR, latest_date, read_archives, reprice
from portfolio_api import (
    ReportCache,
    add_day_change,
    carry_forward,
    history_series,
    performance_payload,
    portfolio_summary,
    positions_page,
    price_validation_summary,
)


HERE = Path(__file__).parent
UI_DIR = HERE / "ui"
SECTORS_FILE = HERE / "sectors.json"
ARCHIVE_DIR = Path(os.environ.get("FLEX_ARCHIVE_DIR", str(DEFAULT_ARCHIVE_DIR)))
PERFORMANCE_FILE = Path(
    os.environ.get(
        "FLEX_PERFORMANCE_FILE",
        str(ARCHIVE_DIR.parent / "performance" / "portfolio.json"),
    )
)
LSEG_HEALTH_URL = os.environ.get("LSEG_HEALTH_URL", "http://127.0.0.1:8091/health")


def get_lseg_last_snapshot_at() -> str | None:
    """Return the newest cached quote snapshot time when the feed exposes one."""
    try:
        response = requests.get(LSEG_HEALTH_URL, timeout=2)
        payload = response.json()
        value = payload.get("last_snapshot_at") if isinstance(payload, dict) else None
        return value.strip() if isinstance(value, str) and value.strip() else None
    except (requests.RequestException, ValueError, TypeError, AttributeError) as exc:
        logging.debug("LSEG timestamp unavailable: %s", type(exc).__name__)
        return None


def archive_files(date: str) -> list[Path]:
    return sorted((ARCHIVE_DIR / date).glob("*.xml.gz"))


def load_report() -> dict:
    if not ARCHIVE_DIR.is_dir():
        raise FileNotFoundError(f"Archive directory not found: {ARCHIVE_DIR}")
    date = latest_date(ARCHIVE_DIR)
    paths = archive_files(date)
    positions, lots, accounts = read_archives(paths)
    previous, carried = [], {}
    try:
        previous_date = latest_date(ARCHIVE_DIR, before=date)
    except ValueError:
        previous_date = ""  # first archived day: no previous close, day change stays blank
    if previous_date:
        previous, previous_lots, previous_accounts = read_archives(archive_files(previous_date))
        extra = carry_forward(previous, accounts, previous_date)
        positions += extra
        lots += carry_forward(previous_lots, accounts, previous_date)
        carried = {"from": previous_date,
                   "accounts": sorted(set(previous_accounts) - set(accounts))}
        accounts = {**{k: v for k, v in previous_accounts.items() if k in carried["accounts"]}, **accounts}
    reprice(positions, lots)
    add_day_change(positions, previous)
    return {
        "date": date,
        "files": len(paths),
        "positions": positions,
        "lots": lots,
        "carried_forward": carried,
        "cash": accounts,
        "lseg_last_snapshot_at": get_lseg_last_snapshot_at(),
    }


report_cache = ReportCache(load_report, ttl_seconds=30)


def nav_available(date: str) -> bool:
    """True once the Flex query carries daily NAV totals (EquitySummaryByReportDateInBase total=)."""
    return any(
        re.search(rb'<EquitySummaryByReportDateInBase [^>]*\btotal="', gzip.open(path).read())
        for path in archive_files(date)
    )


def load_history() -> dict:
    # Without NAV, a P&L-only curve misleads (no cash, flows or returns), so it stays off.
    if not nav_available(latest_date(ARCHIVE_DIR)):
        return {"available": False, "points": [],
                "reason": "Portfolio history turns on once the IBKR Flex query includes daily NAV."}
    days, before = [], "99999999"
    while True:
        try:
            date = latest_date(ARCHIVE_DIR, before=before)
        except ValueError:
            break
        positions, _, accounts = read_archives(archive_files(date))
        days.append((date, positions, accounts))
        before = date
    return {"available": True, **history_series(days)}


_encoded: dict[str, tuple[object, bytes, bytes]] = {}
_encoded_lock = threading.Lock()


def shared_json(request: Request, name: str, source: dict, build) -> Response:
    """Encode and gzip a response once per data refresh; every viewer gets the same bytes."""
    with _encoded_lock:
        cached = _encoded.get(name)
        if cached is None or cached[0] is not source:
            body = json.dumps(build(source), separators=(",", ":")).encode()
            cached = (source, body, gzip.compress(body, 6))
            _encoded[name] = cached
    if "gzip" in request.headers.get("accept-encoding", ""):
        return Response(cached[2], media_type="application/json",
                        headers={"Content-Encoding": "gzip", "Vary": "Accept-Encoding"})
    return Response(cached[1], media_type="application/json")


# Statements arrive once a day, so an hour-old parse is still current.
history_cache = ReportCache(load_history, ttl_seconds=3600)
app = FastAPI(
    title="Sinn Fund Portfolio API",
    version="1.0.0",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)
app.add_middleware(GZipMiddleware, minimum_size=1000)


@app.middleware("http")
async def security_headers(request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Content-Security-Policy"] = (
        # style-src-attr allows only style="" attributes (bar widths), not <style> blocks.
        "default-src 'self'; style-src 'self'; style-src-attr 'unsafe-inline'; "
        "script-src 'self'; img-src 'self' data:; "
        "connect-src 'self'; font-src 'self'; base-uri 'none'; form-action 'none'; "
        "frame-ancestors 'none'"
    )
    response.headers["Cache-Control"] = "no-store"
    return response


app.mount("/static", StaticFiles(directory=UI_DIR), name="static")


@app.get("/")
def dashboard():
    return FileResponse(UI_DIR / "index.html")


@app.get("/api/v1", response_class=JSONResponse)
def index():
    return {
        "service": "Sinn Fund portfolio API",
        "version": "1.0.0",
        "read_only": True,
        "pricing": "Both IBKR statement close and delayed LSEG marks are preserved.",
        "endpoints": [
            "/api/v1/portfolio",
            "/api/v1/positions",
            "/api/v1/report",
            "/api/v1/history",
            "/api/v1/sectors",
            "/api/v1/prices/validation",
            "/api/v1/risk",
            "/api/v1/performance",
            "/healthz",
        ],
    }


@app.get("/api/v1/portfolio")
def portfolio(request: Request):
    return shared_json(request, "portfolio", report_cache.get(), portfolio_summary)


@app.get("/api/v1/positions", response_class=JSONResponse)
def positions(
    q: str = Query("", max_length=100),
    account: str = Query("", max_length=100),
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=250),
):
    return positions_page(
        report_cache.get(), query=q, account=account, offset=offset, limit=limit
    )


REPORT_FIELDS = ("date", "positions", "lots", "carried_forward", "cash")


@app.get("/api/v1/report")
def report(request: Request):
    # shortcut: ships every position and lot (~1 MB, ~100 KB gzipped) for client-side grouping; page it if the book grows.
    return shared_json(request, "report", report_cache.get(),
                       lambda data: {key: data[key] for key in REPORT_FIELDS})


@app.get("/api/v1/sectors")
def sectors():
    # Static map rebuilt by build_sectors.py; unknown symbols render as Unclassified.
    return FileResponse(SECTORS_FILE, media_type="application/json")


@app.get("/api/v1/history")
def history(request: Request):
    return shared_json(request, "history", history_cache.get(), lambda data: data)


RISK_FILE = HERE / "risk" / "latest.json"


@app.get("/api/v1/risk")
def risk():
    # Written by risk/risk_job.py (separate venv with Riskfolio-Lib); served as-is.
    if not RISK_FILE.is_file():
        return JSONResponse({"available": False, "reason": "The daily risk report has not run yet."})
    return FileResponse(RISK_FILE, media_type="application/json")


@app.get("/api/v1/performance", response_class=JSONResponse)
def performance(range: str = Query("ALL", max_length=4)):
    return performance_payload(PERFORMANCE_FILE, range)


@app.get("/api/v1/prices/validation")
def prices_validation(request: Request):
    return shared_json(request, "validation", report_cache.get(), price_validation_summary)


@app.get("/healthz", response_class=PlainTextResponse)
def healthz():
    try:
        latest_date(ARCHIVE_DIR)
    except Exception:
        return PlainTextResponse("Flex archive unavailable\n", status_code=503)
    return PlainTextResponse("ok\n")
