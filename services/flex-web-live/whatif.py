"""Pre-trade what-if: how one proposed trade changes the fund's (and an account's) risk.

Uses risk/scenarios.json from the daily risk job: each modelled symbol's last 500 daily
moves and the current USD exposure per account. The trade is added as more exposure and
the book is revalued under the same historical days, so "before" matches the Risk tab.
Pure Python on purpose: the web service does not carry numpy or Riskfolio.
"""
from __future__ import annotations

import math


def var_hist(pnl: list[float], alpha: float) -> float:
    """Riskfolio VaR_Hist: the loss at the ceil(alpha*T)-th worst day."""
    ordered = sorted(pnl)
    return -ordered[math.ceil(alpha * len(ordered)) - 1]


def es_hist(pnl: list[float], alpha: float) -> float:
    """Riskfolio CVaR_Hist (Rockafellar-Uryasev) expected shortfall."""
    ordered = sorted(pnl)
    k = math.ceil(alpha * len(ordered)) - 1
    tail = sum(x - ordered[k] for x in ordered[:k + 1])
    return -ordered[k] - tail / (alpha * len(ordered))


def market_move(pnl: list[float], market: list[float]) -> float | None:
    """Dollar move of the book for a 1% fall in the S&P 500 (same as the risk job)."""
    n = len(pnl)
    if n < 2:
        return None
    mp, mm = sum(pnl) / n, sum(market) / n
    cov = sum((p - mp) * (m - mm) for p, m in zip(pnl, market)) / (n - 1)
    var = sum((m - mm) ** 2 for m in market) / (n - 1)
    return -cov / var * 0.01 if var else None


def book_pnl(book: dict[str, float], returns: dict[str, list[float]], days: int) -> list[float]:
    pnl = [0.0] * days
    for symbol, value in book.items():
        for i, r in enumerate(returns[symbol]):
            pnl[i] += value * r
    return pnl


def risk(pnl: list[float], nlv: float, market: list[float] | None) -> dict:
    def pct(x):
        return round(x / nlv * 100, 3) if nlv > 0 else None
    var95, var99, es = var_hist(pnl, 0.05), var_hist(pnl, 0.01), es_hist(pnl, 0.025)
    move = market_move(pnl, market) if market else None
    return {"var_95": round(var95, 2), "var_99": round(var99, 2), "es_97_5": round(es, 2),
            "var_95_pct_nlv": pct(var95), "es_97_5_pct_nlv": pct(es),
            "sp500_down_1pct": round(move, 2) if move is not None else None}


def gross_by_sector(book: dict[str, float], sectors: dict[str, str]) -> dict[str, float]:
    out: dict[str, float] = {}
    for symbol, value in book.items():
        name = sectors.get(symbol, "Unclassified")
        out[name] = out.get(name, 0.0) + abs(value)
    return out


def what_if(data: dict, sectors: dict[str, str], symbol: str, quantity: float,
            account: str = "", price: float | None = None) -> dict:
    """Raises ValueError with a user-facing message when the trade cannot be modelled."""
    symbol = symbol.strip().upper()
    returns = data["returns"]
    if symbol not in returns:
        raise ValueError(f"{symbol} has no LSEG daily price history in the risk data, so it cannot be "
                         "modelled yet (options and warrants are not modelled).")
    if account and account not in data["nlv"]:
        raise ValueError(f"Unknown account {account}.")
    price = price or data["last_close"][symbol]
    # shortcut: assumes a USD price; foreign listings would need IBKR's FX rate.
    value = quantity * price
    days = len(data["dates"])
    market = returns.get(data.get("market", ""))

    fund_book: dict[str, float] = {}
    for holdings in data["exposure"].values():
        for s, v in holdings.items():
            fund_book[s] = fund_book.get(s, 0.0) + v
    trade = book_pnl({symbol: value}, returns, days)

    def compare(book: dict[str, float], nlv: float) -> dict:
        before = book_pnl(book, returns, days)
        after = [b + t for b, t in zip(before, trade)]
        result = {"before": risk(before, nlv, market), "after": risk(after, nlv, market)}
        # Trade's share of the new expected shortfall: its average P&L on the after-book's worst 2.5% of days.
        k = math.ceil(0.025 * days)
        tail = sorted(range(days), key=after.__getitem__)[:k]
        result["trade_es_contribution"] = round(-sum(trade[i] for i in tail) / k, 2)
        new_book = {**book, symbol: book.get(symbol, 0.0) + value}
        g_before, g_after = gross_by_sector(book, sectors), gross_by_sector(new_book, sectors)
        sector = sectors.get(symbol, "Unclassified")
        result["sector"] = {
            "name": sector,
            "weight_before_pct": round(g_before.get(sector, 0.0) / (sum(g_before.values()) or 1) * 100, 2),
            "weight_after_pct": round(g_after.get(sector, 0.0) / (sum(g_after.values()) or 1) * 100, 2),
        }
        worst = sorted(range(days), key=before.__getitem__)[:5]
        result["worst_days"] = [{"date": data["dates"][i], "before": round(before[i], 2),
                                 "trade": round(trade[i], 2), "after": round(after[i], 2)} for i in worst]
        return result

    out = {
        "as_of": data.get("as_of"), "scenarios": days,
        "trade": {"symbol": symbol, "quantity": quantity, "price": round(price, 4), "value": round(value, 2),
                  "account": account or None, "sector": sectors.get(symbol, "Unclassified"),
                  "stand_alone_var_95": round(var_hist(trade, 0.05), 2)},
        "fund": compare(fund_book, sum(data["nlv"].values()) + 0.0),
    }
    if account:
        out["account"] = compare(dict(data["exposure"].get(account, {})), data["nlv"][account])
    return out
