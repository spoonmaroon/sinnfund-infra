#!/usr/bin/env python3
"""Daily risk report for the dashboard's Risk management tab.

1-day historical simulation with frozen holdings: today's USD exposure in each
stock is revalued under each of the last WINDOW days' actual LSEG price moves.
VaR/ES and per-position risk contributions use Riskfolio-Lib.

Runs in /opt/flex-web-live/risk-venv (numpy, pandas, riskfolio-lib):
    risk-venv/bin/python risk/risk_job.py
Reads positions/cash from the portfolio API and closes from risk/prices.csv;
writes risk/latest.json, which /api/v1/risk serves.
"""
from __future__ import annotations

import json
import math
import os
import sys
import tempfile
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import urlopen

import numpy as np
import pandas as pd
import riskfolio as rp

HERE = Path(__file__).resolve().parent
REPORT_URL = os.environ.get("PORTFOLIO_REPORT_URL", "http://127.0.0.1:8002/api/v1/report")
# shortcut: closes come from a one-off backfill with a personal LSEG login; read
# lseg-history-api (127.0.0.1:8092) instead once the server's history refresh works.
PRICES = Path(os.environ.get("RISK_PRICES", HERE / "prices.csv"))
SECTORS = HERE.parent / "sectors.json"
OUT = HERE / "latest.json"
WINDOW = 500        # scenarios (trading days)
MIN_HISTORY = 250   # a stock needs about a year of closes to be modelled
MARKET = "IVV"      # S&P 500 proxy for the market-sensitivity line


def number(value) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return 0.0
    return result if math.isfinite(result) else 0.0


def usd_value(row: dict) -> float:
    """Flex values are in the position's currency; convert with IBKR's rate."""
    return number(row.get("market_value")) * (number(row.get("fx_rate_to_base")) or 1.0)


def returns_matrix(prices: pd.DataFrame, window: int = WINDOW) -> tuple[pd.DataFrame, dict]:
    """Daily simple returns (dates x symbols) for the last `window` days.

    Returns the matrix and {symbol: reason} for symbols left out.
    """
    wide = prices.pivot_table(index="date", columns="symbol", values="close").sort_index()
    wide = wide.iloc[-(window + 1):]
    counts = wide.notna().sum()
    short = {s: f"only {int(n)} days of price history" for s, n in counts.items() if n < MIN_HISTORY}
    last = wide.apply(lambda col: col.last_valid_index())
    latest = pd.Timestamp(wide.index[-1])
    for s, day in last.items():
        if s not in short and day is not None and (latest - pd.Timestamp(day)).days > 10:
            short[s] = f"no LSEG price since {day}"  # delisted or stale: frozen prices would understate risk
    wide = wide.drop(columns=list(short))
    # A missing close (holiday on that listing) means no move that day.
    returns = wide.ffill().pct_change(fill_method=None).iloc[1:].fillna(0.0)
    return returns, short


def metrics(pnl: pd.Series, nlv: float) -> dict:
    values = pnl.to_numpy()
    var95 = rp.RiskFunctions.VaR_Hist(values, alpha=0.05)
    var99 = rp.RiskFunctions.VaR_Hist(values, alpha=0.01)
    es975 = rp.RiskFunctions.CVaR_Hist(values, alpha=0.025)
    vol = float(values.std(ddof=1) * math.sqrt(252))

    def pct(x):
        return round(x / nlv * 100, 3) if nlv > 0 else None

    return {
        "var_95": round(var95, 2), "var_99": round(var99, 2), "es_97_5": round(es975, 2),
        "var_95_pct_nlv": pct(var95), "var_99_pct_nlv": pct(var99), "es_97_5_pct_nlv": pct(es975),
        "annual_vol": round(vol, 2), "annual_vol_pct_nlv": pct(vol),
    }


def build_report(report: dict, prices: pd.DataFrame, sectors: dict[str, str]) -> dict:
    returns, short = returns_matrix(prices)
    cash = {account: number(value) for account, value in report.get("cash", {}).items()}
    nlv = defaultdict(float, cash)
    exposure: dict[tuple[str, str], float] = defaultdict(float)
    excluded = []
    for row in report.get("positions", []):
        account, symbol, value = row.get("account_id", ""), str(row.get("symbol", "")).upper(), usd_value(row)
        nlv[account] += value
        if row.get("asset_category") != "STK":
            reason = "options/warrants are not modelled yet"
        elif symbol in short:
            reason = short[symbol]
        elif symbol not in returns.columns:
            reason = "no LSEG price history"
        else:
            exposure[(account, symbol)] += value
            continue
        excluded.append({"account": account, "symbol": symbol, "value": round(value, 2), "reason": reason})

    held = pd.Series(exposure)  # index: (account, symbol)
    by_symbol = held.groupby(level=1).sum()
    symbols = list(by_symbol.index)
    R = returns[symbols]
    fund_pnl = R @ by_symbol
    fund_nlv = sum(nlv.values())

    # Component ES: Riskfolio's contributions sum to the fund's 97.5% expected shortfall.
    contrib = rp.Risk_Contribution(by_symbol.to_frame().values, R, rm="CVaR", alpha=0.025)
    contrib = pd.Series(np.asarray(contrib).ravel(), index=symbols)
    by_sector = defaultdict(float)
    for symbol, value in contrib.items():
        by_sector[sectors.get(symbol, "Unclassified")] += float(value)

    accounts = []
    for account, book in sorted(held.groupby(level=0), key=lambda item: -nlv[item[0]]):
        book = book.droplevel(0)
        accounts.append({"id": account, "nlv": round(nlv[account], 2),
                         "modelled": round(float(book.sum()), 2), **metrics(R[book.index] @ book, nlv[account])})

    market = None
    if MARKET in returns.columns:
        m = returns[MARKET]
        # Dollar move of the modelled book for a 1% fall in the S&P 500.
        market = round(-float(np.cov(fund_pnl, m)[0, 1] / m.var()) * 0.01, 2)

    worst = fund_pnl.nsmallest(5)
    gross = sum(abs(usd_value(r)) for r in report.get("positions", []))
    modelled_gross = float(by_symbol.abs().sum())
    return {
        "available": True,
        "as_of": report.get("date"),
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "prices_through": str(returns.index[-1]),
        "method": (f"1-day historical simulation, frozen holdings, {len(R)} daily LSEG moves "
                   f"({returns.index[0]} to {returns.index[-1]}). VaR is the loss exceeded on 5% / 1% "
                   "of days; expected shortfall is the average of the worst 2.5%. Foreign stocks use "
                   "local-currency moves (FX moves not included)."),
        "scenarios": len(R),
        "fund": {"nlv": round(fund_nlv, 2), **metrics(fund_pnl, fund_nlv),
                 "sp500_down_1pct": market},
        "accounts": accounts,
        "top_contributors": [
            {"symbol": s, "sector": sectors.get(s, "Unclassified"), "exposure": round(float(by_symbol[s]), 2),
             "es_contribution": round(float(c), 2)}
            for s, c in contrib.sort_values(ascending=False).head(15).items()
        ],
        "sectors": sorted(({"sector": k, "es_contribution": round(v, 2)} for k, v in by_sector.items()),
                          key=lambda x: -x["es_contribution"]),
        "worst_days": [{"date": str(d), "pnl": round(float(v), 2)} for d, v in worst.items()],
        "coverage": {"gross": round(gross, 2), "modelled_gross": round(modelled_gross, 2),
                     "modelled_pct": round(modelled_gross / gross * 100, 2) if gross else None,
                     "excluded": sorted(excluded, key=lambda x: -abs(x["value"]))},
    }


def main() -> int:
    with urlopen(REPORT_URL, timeout=60) as response:
        report = json.load(response)
    prices = pd.read_csv(PRICES, dtype={"symbol": str})
    sectors = json.loads(SECTORS.read_text()).get("sectors", {}) if SECTORS.is_file() else {}
    result = build_report(report, prices, sectors)
    with tempfile.NamedTemporaryFile("w", dir=OUT.parent, delete=False, suffix=".tmp") as fh:
        json.dump(result, fh, allow_nan=False)
    os.chmod(fh.name, 0o644)
    os.replace(fh.name, OUT)  # readers never see a half-written report
    fund = result["fund"]
    print(f"{result['as_of']}: VaR95 ${fund['var_95']:,.0f} ({fund['var_95_pct_nlv']}% NLV), "
          f"ES97.5 ${fund['es_97_5']:,.0f}, coverage {result['coverage']['modelled_pct']}%")
    return 0


if __name__ == "__main__":
    sys.exit(main())
