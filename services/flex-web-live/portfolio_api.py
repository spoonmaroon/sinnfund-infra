"""Read-only API helpers for the portfolio dashboard."""

from __future__ import annotations

import json
import logging
import statistics
import threading
import time
from collections import defaultdict
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Callable
from zoneinfo import ZoneInfo

NEW_YORK = ZoneInfo("America/New_York")
LOG = logging.getLogger(__name__)


RANGES = ("1D", "1W", "1M", "3M", "YTD", "1Y", "ALL")


def number(value: object) -> Decimal:
    try:
        parsed = Decimal(str(value or "0"))
        return parsed if parsed.is_finite() else Decimal()
    except (InvalidOperation, ValueError, TypeError):
        return Decimal()


def as_float(value: Decimal) -> float:
    return float(value.quantize(Decimal("0.01")))


def fx(row: dict) -> Decimal:
    """Flex values are in the position's currency; this converts them to USD base."""
    return number(row.get("fx_rate_to_base")) or Decimal(1)


def session_date(timestamp: str) -> str:
    """Trading date (New York) of an ISO timestamp, as YYYYMMDD."""
    try:
        moment = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    except (AttributeError, ValueError):
        return ""
    if moment.tzinfo is None:
        return ""
    return moment.astimezone(NEW_YORK).strftime("%Y%m%d")


def carry_forward(previous_rows: list[dict], accounts: set[str], previous_date: str) -> list[dict]:
    """Rows of accounts IBKR left out of the latest statement, copied from the previous one."""
    return [
        {**row, "carried_from": previous_date}
        for row in previous_rows
        if row.get("account_id") not in accounts
    ]


def add_day_change(positions: list[dict], previous_positions: list[dict]) -> None:
    """Attach the latest session's change to each position.

    If the delayed quote is from a session after the statement date (market open
    or closed but not yet in Flex), the change is delayed price vs statement close.
    Otherwise the statement close is the latest price, so the change is statement
    close vs the previous statement's close.
    """
    previous = {
        (row.get("account_id"), row.get("conid")): number(row.get("mark_price"))
        for row in previous_positions
    }
    for row in positions:
        close = number(row.get("close_mark_price", row.get("mark_price")))
        delayed = number(row.get("delayed_mark_price"))
        quote_day = session_date(row.get("quote_last_event_at") or row.get("quote_snapshot_at") or "")
        if delayed and quote_day > str(row.get("report_date", "")):
            last, base, basis = delayed, close, "delayed_vs_close"
        elif row.get("carried_from"):
            # Carried from yesterday's file with no newer quote: today's move is unknown.
            last, base, basis = close, Decimal(), ""
        else:
            last = close
            base = previous.get((row.get("account_id"), row.get("conid")), Decimal())
            basis = "close_vs_previous_close"
        if not base:
            row.update(previous_close_price="", day_change_price="", day_change_pct="",
                       day_change_value="", day_change_basis="")
            continue
        change = last - base
        size = number(row.get("quantity")) * (number(row.get("multiplier")) or Decimal(1))
        row.update(
            previous_close_price=str(base),
            day_change_price=str(change),
            day_change_pct=str((change / abs(base) * 100).quantize(Decimal("0.0001"))),
            day_change_value=str((change * size * fx(row)).quantize(Decimal("0.01"))),
            day_change_basis=basis,
        )


class ReportCache:
    """Share one report parse across concurrent API requests for a short period."""

    def __init__(self, loader: Callable[[], dict], ttl_seconds: int = 30):
        self.loader = loader
        self.ttl_seconds = ttl_seconds
        self._lock = threading.Lock()
        self._loaded_at = 0.0
        self._value: dict | None = None

    def get(self) -> dict:
        if self._value is not None and time.monotonic() - self._loaded_at < self.ttl_seconds:
            return self._value
        # Another request is already refreshing: hand out the previous snapshot instead of queueing.
        if self._value is not None and not self._lock.acquire(blocking=False):
            return self._value
        if self._value is None:
            self._lock.acquire()
        try:
            if self._value is None or time.monotonic() - self._loaded_at >= self.ttl_seconds:
                try:
                    self._value = self.loader()
                except Exception:
                    if self._value is None:
                        raise
                    LOG.exception("Refresh failed; serving the previous snapshot")
                self._loaded_at = time.monotonic()
        finally:
            self._lock.release()
        return self._value


def portfolio_summary(report: dict) -> dict:
    positions = report.get("positions", [])
    account_values: dict[str, Decimal] = defaultdict(Decimal)
    account_pnl: dict[str, Decimal] = defaultdict(Decimal)
    allocation: dict[str, Decimal] = defaultdict(Decimal)
    total_value = Decimal()
    total_pnl = Decimal()
    close_value = Decimal()
    close_pnl = Decimal()
    day_change = Decimal()
    repriced = 0

    for row in positions:
        rate = fx(row)
        value = number(row.get("market_value")) * rate
        pnl = number(row.get("unrealized_pnl")) * rate
        account = str(row.get("account_id") or "Unknown")
        category = str(row.get("asset_category") or "Other")
        total_value += value
        total_pnl += pnl
        close_value += number(row.get("close_market_value", row.get("market_value"))) * rate
        close_pnl += number(row.get("close_unrealized_pnl", row.get("unrealized_pnl"))) * rate
        day_change += number(row.get("day_change_value"))
        account_values[account] += value
        account_pnl[account] += pnl
        allocation[category] += abs(value)
        if int(number(row.get("psuedo-live updated"))):
            repriced += 1

    cash = {account: number(value) for account, value in report.get("cash", {}).items()}
    for account in cash:
        account_values.setdefault(account, Decimal())
    # Net liquidation value ≈ positions + ending cash (excludes accruals).
    nlv = {account: value + cash.get(account, Decimal()) for account, value in account_values.items()}
    total_nlv = sum(nlv.values(), Decimal())
    accounts = [
        {
            "id": account,
            "value": as_float(account_values[account]),
            "cash": as_float(cash.get(account, Decimal())),
            "net_liquidation_value": as_float(nlv[account]),
            "unrealized_pnl": as_float(account_pnl[account]),
            "allocation": float((nlv[account] / total_nlv * 100).quantize(Decimal("0.01")))
            if total_nlv
            else 0.0,
        }
        for account in sorted(nlv, key=lambda item: nlv[item], reverse=True)
    ]
    gross_allocation = sum(allocation.values(), Decimal())
    allocations = [
        {
            "category": category,
            "value": as_float(value),
            "allocation": float((value / gross_allocation * 100).quantize(Decimal("0.01")))
            if gross_allocation
            else 0.0,
        }
        for category, value in sorted(allocation.items(), key=lambda item: item[1], reverse=True)
    ]

    return {
        "as_of": report.get("date"),
        "quote_snapshot_at": report.get("lseg_last_snapshot_at"),
        "currency": "USD",
        "totals": {
            "position_value": as_float(total_value),
            "unrealized_pnl": as_float(total_pnl),
            "close_position_value": as_float(close_value),
            "close_unrealized_pnl": as_float(close_pnl),
            "delayed_value_change": as_float(total_value - close_value),
            "day_change": as_float(day_change),
            "cash": as_float(sum(cash.values(), Decimal())),
            "net_liquidation_value": as_float(total_nlv),
            "positions": len(positions),
            "accounts": len(nlv),
            "tax_lots": len(report.get("lots", [])),
            "repriced_positions": repriced,
        },
        "accounts": accounts,
        "allocation": allocations,
        "data_note": "Position values exclude uninvested cash and are not total account NAV.",
    }


def price_validation_summary(report: dict) -> dict:
    """Summarize close-versus-delayed checks once per resolved symbol."""
    positions = report.get("positions", [])
    by_symbol = {}
    updated_positions = 0
    large_moves = 0
    for row in positions:
        if int(number(row.get("psuedo-live updated"))):
            updated_positions += 1
            by_symbol.setdefault(str(row.get("symbol") or ""), row)
            if row.get("price_validation") == "accepted_large_move":
                large_moves += 1

    gaps = [
        abs(float(number(row.get("price_change_from_close_pct"))))
        for row in by_symbol.values()
    ]

    def percentile(values: list[float], fraction: float) -> float | None:
        if not values:
            return None
        ordered = sorted(values)
        return round(ordered[round((len(ordered) - 1) * fraction)], 4)

    value_change = sum(
        (
            number(row.get("delayed_market_value"))
            - number(row.get("close_market_value"))
        ) * fx(row)
        for row in positions
        if int(number(row.get("psuedo-live updated")))
    )
    return {
        "as_of": report.get("date"),
        "quote_snapshot_at": report.get("lseg_last_snapshot_at"),
        "positions": len(positions),
        "updated_positions": updated_positions,
        "updated_symbols": len(by_symbol),
        "coverage_pct": round(updated_positions / len(positions) * 100, 2)
        if positions
        else 0.0,
        "absolute_price_gap_pct": {
            "median": round(statistics.median(gaps), 4) if gaps else None,
            "p90": percentile(gaps, 0.90),
            "p95": percentile(gaps, 0.95),
            "max": round(max(gaps), 4) if gaps else None,
        },
        "moves_over_10_pct": sum(gap > 10 for gap in gaps),
        "moves_over_20_pct": sum(gap > 20 for gap in gaps),
        "positions_flagged_large_move": large_moves,
        "aggregate_value_change": as_float(value_change),
        "status": "accepted_with_flags" if large_moves else "accepted",
        "method": (
            "Delayed RIC mappings are identity-checked against Flex metadata. "
            "Both statement close and delayed marks remain in every updated row."
        ),
    }


def positions_page(
    report: dict,
    *,
    query: str = "",
    account: str = "",
    offset: int = 0,
    limit: int = 50,
) -> dict:
    query = query.strip().casefold()
    lot_counts: dict[tuple[str, str], int] = defaultdict(int)
    for lot in report.get("lots", []):
        lot_counts[(str(lot.get("account_id", "")), str(lot.get("conid", "")))] += 1

    rows = []
    for row in report.get("positions", []):
        if account and row.get("account_id") != account:
            continue
        haystack = " ".join(
            str(row.get(key, ""))
            for key in ("symbol", "description", "account_id", "book", "asset_category")
        ).casefold()
        if query and query not in haystack:
            continue
        item = dict(row)
        item["lot_count"] = lot_counts[(str(row.get("account_id", "")), str(row.get("conid", "")))]
        rows.append(item)

    rows.sort(key=lambda row: abs(number(row.get("market_value"))), reverse=True)
    return {"total": len(rows), "offset": offset, "limit": limit, "items": rows[offset : offset + limit]}


def history_series(days: list[tuple[str, list[dict], set[str]]]) -> dict:
    """Daily position value and mark-to-market P&L from consecutive Flex statements.

    P&L for a day is yesterday's quantity times the change in IBKR's close, so
    deposits and withdrawals do not move it.
    shortcut: ignores intraday trade P&L, dividends and fees; switch to broker TWR once
    the Flex query carries NAV/TWR fields.
    """
    points = []
    previous: dict = {}
    cumulative = Decimal()
    previous_rows: list[dict] = []
    previous_date = ""
    for date, positions, accounts in sorted(days, key=lambda day: day[0]):
        # An account IBKR excluded that day keeps its last positions (zero P&L that day).
        positions = positions + carry_forward(previous_rows, accounts, previous_date)
        previous_rows, previous_date = positions, date
        current = {}
        value = Decimal()
        pnl = Decimal()
        for row in positions:
            key = (row.get("account_id"), row.get("conid"))
            rate = fx(row)
            mark = number(row.get("mark_price"))
            size = number(row.get("quantity")) * (number(row.get("multiplier")) or Decimal(1))
            value += number(row.get("market_value")) * rate
            if key in previous:
                prior_size, prior_mark = previous[key]
                pnl += prior_size * (mark - prior_mark) * rate
            current[key] = (size, mark)
        if previous:
            cumulative += pnl
        points.append({
            "date": date,
            "position_value": as_float(value),
            "day_pnl": as_float(pnl) if previous else None,
            "cumulative_pnl": as_float(cumulative),
        })
        previous = current
    return {
        "points": points,
        "method": "Mark-to-market P&L on held positions between IBKR statement closes.",
    }


def performance_payload(path: Path, selected_range: str) -> dict:
    selected_range = selected_range.upper()
    if selected_range not in RANGES:
        selected_range = "ALL"

    unavailable = {
        "available": False,
        "range": selected_range,
        "currency": "USD",
        "return": None,
        "points": [],
        "periods": {period: None for period in RANGES},
        "reason": "The saved IBKR Flex query does not include daily NAV or time-weighted return fields.",
    }
    if not path.is_file():
        return unavailable
    try:
        payload = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError, TypeError):
        return unavailable
    if not isinstance(payload, dict) or not isinstance(payload.get("points"), list):
        return unavailable

    ranges = payload.get("ranges", {})
    selected = ranges.get(selected_range, {}) if isinstance(ranges, dict) else {}
    return {
        "available": bool(payload.get("points")),
        "range": selected_range,
        "currency": payload.get("currency", "USD"),
        "return": selected.get("return") if isinstance(selected, dict) else None,
        "points": selected.get("points", payload["points"]) if isinstance(selected, dict) else payload["points"],
        "periods": {
            period: ranges.get(period, {}).get("return")
            if isinstance(ranges.get(period), dict)
            else None
            for period in RANGES
        },
        "reason": None,
        "method": payload.get("method", "TWR"),
        "generated_at": payload.get("generated_at"),
    }
