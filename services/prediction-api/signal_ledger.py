"""Authenticated pending signals and on-demand LIVE target CSV exports."""

import base64
import csv
import io
from contextlib import contextmanager
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import fcntl
import hashlib
import hmac
import logging
import math
import os
from pathlib import Path
import re
import secrets
import shutil
import tempfile
import time
from typing import Literal

from dotenv import load_dotenv
from fastapi import APIRouter, Depends, Header, HTTPException, Response
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator
import requests

import paper_signals


load_dotenv(Path(__file__).with_name("signal-runtime.conf"), override=False)
logging.getLogger("requests").setLevel(logging.WARNING)
logging.getLogger("urllib3").setLevel(logging.WARNING)

router = APIRouter(prefix="/signals", tags=["Signal ledger"])
LOG = logging.getLogger(__name__)
LEDGER_FIELDS = (
    "event", "at", "actor", "owner", "signal_id", "revision", "destination",
    "ticker", "price", "limit_price", "timestamp", "status", "direction", "reason",
    "current_price", "price_source", "price_timestamp", "export_id", "export_file",
)
LEGACY_LEDGER_FIELDS = tuple(field for field in LEDGER_FIELDS if field != "limit_price")
_ledger_cache = None


class SignalCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    signal_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,48}$")
    destination: Literal["LIVE", "PAPER"]
    ticker: str = Field(pattern=r"^[A-Z0-9][A-Z0-9 .-]{0,19}$")
    price: float | int = Field(gt=0, allow_inf_nan=False)
    limit_price: float | int | None = Field(default=None, gt=0, le=1000, allow_inf_nan=False)
    timestamp: int = Field(gt=0)

    @model_validator(mode="after")
    def destination_limit(self):
        if self.destination == "PAPER" and self.limit_price is None:
            raise ValueError("PAPER signals require limit_price")
        if self.destination == "LIVE" and self.limit_price is not None:
            raise ValueError("LIVE signals cannot include limit_price")
        if (self.limit_price is not None and
                Decimal(str(self.limit_price)).as_tuple().exponent < -2):
            raise ValueError("limit_price must have at most two decimal places")
        return self


class SignalEdit(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    signal_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,48}$")
    revision: int = Field(gt=0)
    destination: Literal["LIVE", "PAPER"] | None = None
    ticker: str | None = Field(default=None, pattern=r"^[A-Z0-9][A-Z0-9 .-]{0,19}$")
    price: float | int | None = Field(default=None, gt=0, allow_inf_nan=False)
    limit_price: float | int | None = Field(default=None, gt=0, le=1000, allow_inf_nan=False)
    timestamp: int | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def has_change(self):
        changed = self.model_fields_set - {"signal_id", "revision"}
        if not changed:
            raise ValueError("at least one signal field must change")
        if any(getattr(self, field) is None for field in changed):
            raise ValueError("edited signal fields cannot be null")
        return self


class SignalDelete(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    signal_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,48}$")
    revision: int = Field(gt=0)


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def data_root():
    return Path(os.getenv("SIGNAL_DATA_DIR", "data/signals"))


def keys_path(scope):
    name = {"sender": "senders.htpasswd", "exporter": "exporters.htpasswd", "admin": "admins.htpasswd"}[scope]
    variable = "SIGNAL_" + scope.upper() + "_KEYS_FILE"
    return Path(os.getenv(variable, str(data_root() / name)))


def authenticate(authorization, scope):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "Missing signal API credential")
    token = authorization[7:]
    if not token:
        raise HTTPException(401, "Invalid signal API credential")
    scopes = (scope,) if scope == "admin" else (scope, "admin")
    paths = [path for path in map(keys_path, scopes) if path.exists()]
    if not paths:
        raise HTTPException(503, f"{keys_path(scope).name} is not configured")
    digest = base64.b64encode(hashlib.sha1(token.encode()).digest()).decode()
    for path in paths:
        for line in path.read_text().splitlines():
            if not line or line.startswith("#") or ":" not in line:
                continue
            name, stored = line.split(":", 1)
            if (re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", name) and stored.startswith("{SHA}")
                    and hmac.compare_digest(stored[5:], digest)):
                return name
    raise HTTPException(401, "Invalid signal API credential")


def sender(authorization: str | None = Header(default=None)):
    return authenticate(authorization, "sender")


def exporter(authorization: str | None = Header(default=None)):
    return authenticate(authorization, "exporter")


def administrator(authorization: str | None = Header(default=None)):
    return authenticate(authorization, "admin")


def save_ledger(path, rows):
    """Atomically append new events without re-encoding the existing audit history."""
    # shortcut: copy the CSV for atomic snapshots; use transactional storage if copy cost dominates.
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temp = None
    try:
        with tempfile.NamedTemporaryFile("w", newline="", dir=path.parent, delete=False) as file:
            temp = Path(file.name)
            writer = csv.DictWriter(file, fieldnames=LEDGER_FIELDS)
            if path.exists():
                with path.open(newline="") as source:
                    reader = csv.DictReader(source)
                    if tuple(reader.fieldnames or ()) == LEDGER_FIELDS:
                        shutil.copyfile(path, temp)
                        file.seek(0, os.SEEK_END)
                        with temp.open("rb") as copied:
                            copied.seek(-1, os.SEEK_END)
                            if copied.read(1) not in (b"\n", b"\r"):
                                file.write("\n")
                    elif tuple(reader.fieldnames or ()) == LEGACY_LEDGER_FIELDS:
                        writer.writeheader()
                        writer.writerows({**row, "limit_price": ""} for row in reader)
                    else:
                        raise RuntimeError("Signal ledger columns are invalid")
            else:
                writer.writeheader()
            writer.writerows(rows)
            file.flush()
            os.fsync(file.fileno())
        os.chmod(temp, 0o640)
        temp.replace(path)
        fsync_directory(path.parent)
    finally:
        if temp is not None:
            temp.unlink(missing_ok=True)


def fsync_directory(path):
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


@contextmanager
def locked_ledger():
    global _ledger_cache
    root = data_root()
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (root / ".ledger.lock").open("a") as lock:
        os.chmod(lock.name, 0o600)
        fcntl.flock(lock, fcntl.LOCK_EX)
        path = root / "ledger.csv"
        signature = ledger_signature(path)
        if _ledger_cache is not None and _ledger_cache[:2] == (path, signature):
            records = _ledger_cache[2]
        elif path.exists():
            with path.open(newline="") as file:
                reader = csv.DictReader(file)
                fields = tuple(reader.fieldnames or ())
                if fields not in (LEDGER_FIELDS, LEGACY_LEDGER_FIELDS):
                    raise RuntimeError("Signal ledger columns are invalid")
                rows = list(reader)
                if fields == LEGACY_LEDGER_FIELDS:
                    for row in rows:
                        row["limit_price"] = ""
                records = records_from_rows(rows)
        else:
            records = {}
        _ledger_cache = (path, signature, records)
        added = []
        # Request-local mapping prevents an unsuccessful create from polluting the cache.
        yield path, added, records.copy()
        if added and ledger_signature(path) != signature:
            _ledger_cache = (path, ledger_signature(path), records_from_rows(added, records))


def ledger_signature(path):
    try:
        stat = path.stat()
    except FileNotFoundError:
        return None
    return stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns


def us_ticker(ticker):
    """LSEG US RIC -> exchange ticker (ZBH.N -> ZBH, ZBRA.OQ -> ZBRA); other tickers unchanged.

    Senders may use LSEG RICs, but Twelve Data and the IBKR targets file need the plain ticker.
    shortcut: NYSE American (.A) is left alone because it collides with share classes like BF.A.
    """
    return re.sub(r"\.(N|OQ|O)$", "", ticker)


def fetch_quotes(tickers):
    key = os.getenv("TWELVE_DATA_API_KEY")
    if not key:
        raise RuntimeError("Missing TWELVE_DATA_API_KEY")
    try:
        response = requests.get(
            "https://api.twelvedata.com/quote",
            params={"symbol": ",".join(sorted({us_ticker(t) for t in tickers}))},
            headers={"Authorization": "apikey " + key, "Cache-Control": "no-cache"},
            timeout=8,
        )
        response.raise_for_status()
        payload = response.json()
        fetched_at = time.time()
    except (requests.RequestException, ValueError) as error:
        LOG.warning("Signal quote request failed (%s)", type(error).__name__)
        raise RuntimeError("Current prices are unavailable") from None

    items = [payload] if isinstance(payload, dict) and "symbol" in payload else (
        list(payload.values()) if isinstance(payload, dict) else [])
    quotes = {}
    for item in items:
        if not isinstance(item, dict) or not isinstance(item.get("symbol"), str):
            continue
        try:
            price = Decimal(str(item["close"]))
        except (KeyError, InvalidOperation):
            continue
        if not price.is_finite() or price <= 0:
            continue
        ticker = item["symbol"].upper()
        quotes[ticker] = {
            "price": str(price),
            "source": "twelve_data_quote_close",
            "timestamp": (item.get("last_update_at") or item.get("last_quote_at")
                          or item.get("timestamp")),
            "fetched_at": fetched_at,
        }
    quotes = {t: quotes[us_ticker(t)] for t in tickers if us_ticker(t) in quotes}  # keyed as the sender wrote it
    missing = sorted(set(tickers) - quotes.keys())
    if missing:
        raise RuntimeError("Current prices are unavailable for all requested symbols")
    return quotes


def quote_timestamp(value):
    if isinstance(value, (int, float)):
        number = float(value)
        return number if math.isfinite(number) else None
    if isinstance(value, str):
        try:
            number = float(value)
            return number if math.isfinite(number) else None
        except ValueError:
            try:
                parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=timezone.utc)
                return parsed.timestamp()
            except (ValueError, OverflowError, OSError):
                return None
    return None


def require_fresh_quotes(quotes):
    if not quotes:
        return
    try:
        maximum_age = int(os.getenv("SIGNAL_MAX_QUOTE_AGE_SECONDS", "5400"))
        maximum_fetch_age = int(os.getenv("SIGNAL_MAX_QUOTE_FETCH_AGE_SECONDS", "30"))
    except ValueError:
        maximum_age = maximum_fetch_age = 0
    if maximum_age <= 0 or maximum_fetch_age <= 0:
        raise RuntimeError("Quote freshness is not configured")
    now = time.time()
    for quote in quotes.values():
        observed = quote_timestamp(quote.get("timestamp"))
        fetched = quote_timestamp(quote.get("fetched_at"))
        if fetched is None or now - fetched > maximum_fetch_age or fetched - now > 5:
            raise RuntimeError("Current prices were not refreshed for this request")
        if observed is None or now - observed > maximum_age or observed - now > 60:
            raise RuntimeError("Current prices are stale or missing timestamps")


def decision(signal, quote):
    target = Decimal(str(signal["price"]))
    current = Decimal(quote["price"])
    if target == current:
        return None
    return "LONG" if target > current else "SHORT"


def signal_from_row(row):
    return {"signal_id": row["signal_id"], "destination": row["destination"],
            "ticker": row["ticker"], "price": float(row["price"]),
            "limit_price": float(row["limit_price"]) if row.get("limit_price") else None,
            "timestamp": int(row["timestamp"])}


def comparison_from_row(row):
    if not row["current_price"]:
        return None
    return {"price": row["current_price"], "source": row["price_source"],
            "timestamp": row["price_timestamp"] or None}


def event_from_row(row):
    event = {"event": row["event"], "at": row["at"], "actor": row["actor"],
             "revision": int(row["revision"]), "signal": signal_from_row(row),
             "status": row["status"]}
    comparison = comparison_from_row(row)
    if comparison:
        event.update(comparison=comparison, direction=row["direction"] or None,
                     reason=row["reason"] or None)
    if row["export_id"]:
        event.update(export_id=row["export_id"], export_file=row["export_file"])
    return event


def records_from_rows(rows, previous=None):
    records = previous.copy() if previous is not None else {}
    for row in rows:
        signal_id = row["signal_id"]
        history = records.get(signal_id, {}).get("history", [])
        history = [*history, event_from_row(row)]
        records[signal_id] = {
            "owner": row["owner"], "revision": int(row["revision"]),
            "status": row["status"], "reason": row["reason"] or None,
            "direction": row["direction"] or None,
            "comparison": comparison_from_row(row), "current": signal_from_row(row),
            "history": history,
        }
    return records


def ledger_row(event, actor, owner, revision, signal, status, direction=None,
               reason=None, quote=None, export_id="", export_file=""):
    return {
        "event": event, "at": utc_now(), "actor": actor, "owner": owner,
        "signal_id": signal["signal_id"], "revision": str(revision),
        "destination": signal["destination"], "ticker": signal["ticker"],
        "price": str(signal["price"]),
        "limit_price": str(signal.get("limit_price") or ""),
        "timestamp": str(signal["timestamp"]),
        "status": status, "direction": direction or "", "reason": reason or "",
        "current_price": quote["price"] if quote else "",
        "price_source": quote["source"] if quote else "",
        "price_timestamp": str(quote.get("timestamp") or "") if quote else "",
        "export_id": export_id, "export_file": export_file,
    }


def confirmation(record, operation):
    return {"operation": operation, "signal_id": record["current"]["signal_id"],
            "owner": record["owner"], "revision": record["revision"],
            "destination": record["current"]["destination"], "status": record["status"],
            "direction": record.get("direction"), "reason": record.get("reason")}


def payload_list(payload):
    items = payload if isinstance(payload, list) else [payload]
    if not 1 <= len(items) <= 100:
        raise HTTPException(422, "request must contain 1-100 signals")
    ids = [item.signal_id for item in items]
    if len(ids) != len(set(ids)):
        raise HTTPException(422, "signal IDs must be unique within a request")
    return items


def validated_signal(value):
    try:
        return SignalCreate.model_validate(value).model_dump()
    except ValidationError as error:
        raise HTTPException(422, error.errors(include_url=False, include_context=False)) from None


def paper_order(signal, direction):
    return paper_signals.Signal(
        signal_id=signal["signal_id"], direction=direction, ticker=signal["ticker"],
        price=float(signal["price"]), timestamp=signal["timestamp"],
        limit_price=float(signal["limit_price"]),
    )


def save_changes(path, rows, changed, queued):
    if not queued:
        if changed:
            save_ledger(path, rows)
        return
    with paper_signals.locked_store() as root:
        prepared = paper_signals.prepare_signals(root, queued)
        # shortcut: two flat-file stores cannot commit atomically; retry repairs a missing queue file.
        if changed:
            save_ledger(path, rows)
        paper_signals.save_prepared(prepared)


@router.post("", status_code=202)
def create(payload: SignalCreate | list[SignalCreate], owner=Depends(sender)):
    items = payload_list(payload)
    with locked_ledger() as (path, rows, existing):
        for item in items:
            record = existing.get(item.signal_id)
            if not record:
                continue
            if record["owner"] != owner:
                raise HTTPException(409, "signal ID already belongs to another sender")
            if record["current"] != item.model_dump():
                raise HTTPException(409, "signal ID already has different content; use POST /signals/edit")
        new_items = [item for item in items if item.signal_id not in existing]
        paper = [item for item in new_items if item.destination == "PAPER"]
        try:
            quotes = fetch_quotes([item.ticker for item in paper]) if paper else {}
            require_fresh_quotes(quotes)
        except RuntimeError as error:
            raise HTTPException(503, str(error)) from None
        result = []
        changed = False
        queued = []
        for item in items:
            value = item.model_dump()
            record = existing.get(item.signal_id)
            if record:
                if (record["status"] == "queued" and record["direction"] and
                        record["current"]["destination"] == "PAPER"):
                    queued.append(paper_order(record["current"], record["direction"]))
                result.append(confirmation(record, "unchanged"))
                continue
            quote = quotes.get(item.ticker) if item.destination == "PAPER" else None
            direction = decision(value, quote) if quote else None
            status = ("rejected" if quote and direction is None else
                      "queued" if quote else "pending")
            reason = "no_direction" if status == "rejected" else None
            row = ledger_row("created", owner, owner, 1, value, status,
                             direction, reason, quote)
            rows.append(row)
            record = records_from_rows([row])[item.signal_id]
            existing[item.signal_id] = record
            result.append(confirmation(record, "created"))
            if status == "queued":
                queued.append(paper_order(value, direction))
            changed = True
        save_changes(path, rows, changed, queued)
        return result


@router.post("/edit")
def edit(payload: SignalEdit | list[SignalEdit], owner=Depends(sender)):
    items = payload_list(payload)
    with locked_ledger() as (path, rows, records):
        changed_records = []
        for item in items:
            record = records.get(item.signal_id)
            if not record or record["owner"] != owner:
                raise HTTPException(404, "signal not found")
            if record["status"] not in ("pending", "rejected") or (
                    record["status"] == "rejected" and record.get("reason") != "no_direction"):
                raise HTTPException(409, "only pending or no_direction signals can be edited")
            if record["revision"] != item.revision:
                raise HTTPException(409, "signal revision changed; fetch it before editing")
            fields = item.model_dump(exclude_unset=True, exclude={"signal_id", "revision"})
            updated = {**record["current"], **fields}
            if fields.get("destination") == "LIVE":
                updated["limit_price"] = None
            updated = validated_signal(updated)
            changed_records.append((record, updated))

        paper_tickers = [updated["ticker"] for record, updated in changed_records
                         if record["current"] != updated and updated["destination"] == "PAPER"]
        try:
            quotes = fetch_quotes(paper_tickers) if paper_tickers else {}
            require_fresh_quotes(quotes)
        except RuntimeError as error:
            raise HTTPException(503, str(error)) from None

        result = []
        queued = []
        for record, updated in changed_records:
            if record["current"] == updated:
                result.append(confirmation(record, "unchanged"))
                continue
            quote = quotes.get(updated["ticker"]) if updated["destination"] == "PAPER" else None
            direction = decision(updated, quote) if quote else None
            revision = record["revision"] + 1
            status = ("rejected" if quote and direction is None else
                      "queued" if quote else "pending")
            reason = "no_direction" if status == "rejected" else None
            row = ledger_row("edited", owner, owner, revision, updated, status,
                             direction, reason, quote)
            rows.append(row)
            new_record = records_from_rows([row])[updated["signal_id"]]
            result.append(confirmation(new_record, "edited"))
            if status == "queued":
                queued.append(paper_order(updated, direction))
        changed = any(record["current"] != updated for record, updated in changed_records)
        save_changes(path, rows, changed, queued)
        return result


@router.post("/delete")
def delete_signal(payload: SignalDelete | list[SignalDelete], owner=Depends(sender)):
    items = payload_list(payload)
    with locked_ledger() as (path, rows, records):
        selected = []
        for item in items:
            record = records.get(item.signal_id)
            if not record or record["owner"] != owner:
                raise HTTPException(404, "signal not found")
            if record["status"] not in ("pending", "rejected") or (
                    record["status"] == "rejected" and record.get("reason") != "no_direction"):
                raise HTTPException(409, "only pending or no_direction signals can be deleted")
            if record["revision"] != item.revision:
                raise HTTPException(409, "signal revision changed; fetch it before deleting")
            selected.append(record)

        result = []
        for record in selected:
            revision = record["revision"] + 1
            row = ledger_row("deleted", owner, owner, revision, record["current"],
                             "deleted", record["direction"], "deleted_by_sender",
                             record["comparison"])
            rows.append(row)
            result.append(confirmation(records_from_rows([row])[row["signal_id"]], "deleted"))
        save_ledger(path, rows)
        return result


@router.get("")
def list_signals(owner=Depends(sender)):
    with locked_ledger() as (_, _, records):
        return [
            {"signal": record["current"], "revision": record["revision"],
             "status": record["status"], "direction": record["direction"],
             "reason": record["reason"]}
            for record in records.values() if record["owner"] == owner
        ]


# Declared before /{signal_id} so the path is not read as a signal ID.
def pending_live(records):
    return sorted((record for record in records.values()
                   if record["status"] == "pending" and record["current"]["destination"] == "LIVE"),
                  key=lambda record: record["history"][0]["at"])


def lseg_prices(tickers):
    """Delayed LSEG prices keyed by ticker as sent, from the local LSEG feed's /snapshot route."""
    url = os.getenv("LSEG_FEED_URL", "http://127.0.0.1:8091") + "/snapshot"
    response = requests.get(url, params={"symbols": ",".join(sorted(set(tickers)))}, timeout=90)
    response.raise_for_status()
    return {ticker: quote["price"] for ticker, quote in response.json()["quotes"].items()}


# Declared before /{signal_id} so these paths are not read as signal IDs.
@router.get("/pending")
def pending_signals(_reader=Depends(sender)):
    """Every sender's pending LIVE signals, for the dashboard's Signals tab."""
    with locked_ledger() as (_, _, records):
        return [{"analyst": record["owner"], "signal_id": record["current"]["signal_id"],
                 "ticker": record["current"]["ticker"], "target_price": record["current"]["price"],
                 "sent_at": record["history"][0]["at"]}
                for record in pending_live(records)]


@router.get("/portfolio.csv")
def portfolio_csv(_reader=Depends(sender)):
    """Pending LIVE signals as the Implementation tool's account,symbol,target file (ADR-0034).

    Equal weight: each signal is +1/N if its target is above the LSEG price, -1/N if below. Signals on one
    stock are added, since the tool keeps only the last row per symbol. Read-only: nothing marked exported.
    """
    account = os.getenv("SIGNAL_LIVE_ACCOUNT")
    if not account:
        raise HTTPException(503, "Missing SIGNAL_LIVE_ACCOUNT")
    with locked_ledger() as (_, _, records):
        pending = [record["current"] for record in pending_live(records)]
    try:
        prices = lseg_prices([signal["ticker"] for signal in pending]) if pending else {}
    except (requests.RequestException, KeyError, ValueError):
        raise HTTPException(503, "LSEG prices unavailable; try again later") from None
    sides, left_out = {}, []
    for signal in pending:
        price = prices.get(signal["ticker"])
        direction = decision(signal, {"price": str(price)}) if price else None
        if direction is None:
            left_out.append(signal["signal_id"])
            continue
        sides.setdefault(us_ticker(signal["ticker"]), []).append(1 if direction == "LONG" else -1)
    count = sum(len(signs) for signs in sides.values())
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(["account", "symbol", "target"])
    writer.writerows([account, symbol, round(sum(signs) / count, 6)] for symbol, signs in sides.items())
    return Response(out.getvalue(), media_type="text/csv", headers={
        "Content-Disposition": 'attachment; filename="signals-targets.csv"',
        "X-Signals-Left-Out": ",".join(left_out)})


@router.get("/{signal_id}")
def get_signal(signal_id: str, owner=Depends(sender)):
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,48}", signal_id):
        raise HTTPException(404, "signal not found")
    with locked_ledger() as (_, _, records):
        record = records.get(signal_id)
        if not record or record["owner"] != owner:
            raise HTTPException(404, "signal not found")
        return record


@router.post("/clear")
def clear_live(operator=Depends(administrator)):
    with locked_ledger() as (path, rows, records):
        pending = pending_live(records)
        if not pending:
            raise HTTPException(409, "no pending LIVE signals")
        for record in pending:
            rows.append(ledger_row("cleared", operator, record["owner"], record["revision"],
                                   record["current"], "cleared", record["direction"],
                                   "cleared_by_admin", record["comparison"]))
        save_ledger(path, rows)
        return {"cleared": len(pending)}


def write_csv(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with tempfile.NamedTemporaryFile("w", newline="", dir=path.parent, delete=False) as file:
        temp = Path(file.name)
        writer = csv.writer(file)
        writer.writerow(["account", "symbol", "target"])
        writer.writerows(rows)
        file.flush()
        os.fsync(file.fileno())
    try:
        os.chmod(temp, 0o640)
        temp.replace(path)
        fsync_directory(path.parent)
    finally:
        temp.unlink(missing_ok=True)


@router.post("/export")
def export_live(operator=Depends(exporter)):
    account = os.getenv("SIGNAL_LIVE_ACCOUNT")
    if not account:
        raise HTTPException(503, "Missing SIGNAL_LIVE_ACCOUNT")
    with locked_ledger() as (ledger_path, ledger_rows, records):
        pending = pending_live(records)
        if not pending:
            raise HTTPException(409, "no pending LIVE signals")
        symbols = [us_ticker(record["current"]["ticker"]) for record in pending]
        duplicates = sorted({symbol for symbol in symbols if symbols.count(symbol) > 1})
        if duplicates:
            ids = [record["current"]["signal_id"] for record in pending
                   if us_ticker(record["current"]["ticker"]) in duplicates]
            raise HTTPException(409, {"message": "duplicate LIVE symbols", "signal_ids": ids})
        try:
            quotes = fetch_quotes([record["current"]["ticker"] for record in pending])  # keyed as stored
            require_fresh_quotes(quotes)
        except RuntimeError as error:
            raise HTTPException(503, str(error)) from None

        export_id = secrets.token_hex(8)
        now = utc_now()
        filename = f"targets_{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}_{export_id}.csv"
        output = data_root() / "exports" / filename
        rows = []
        rejected = []
        for record in pending:
            signal = record["current"]
            quote = quotes[signal["ticker"]]
            direction = decision(signal, quote)
            if direction is None:
                rejected.append(signal["signal_id"])
                continue
            rows.append([account, us_ticker(signal["ticker"]), "0.05" if direction == "LONG" else "-0.05"])
        # shortcut: a crash after this write can leave an unindexed CSV; exports are infrequent.
        write_csv(output, rows)

        for record in pending:
            signal = record["current"]
            quote = quotes[signal["ticker"]]
            direction = decision(signal, quote)
            status = "rejected" if direction is None else "exported"
            reason = "no_direction" if direction is None else None
            row = ledger_row(status, operator, record["owner"], record["revision"],
                             signal, status, direction, reason, quote, export_id, filename)
            row["at"] = now
            ledger_rows.append(row)
        save_ledger(ledger_path, ledger_rows)
        return {"export_id": export_id, "rows": len(rows),
                "consumed": len(pending), "rejected_signal_ids": rejected}
