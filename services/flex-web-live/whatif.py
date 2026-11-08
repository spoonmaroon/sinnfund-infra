"""Pre-trade what-if: how a basket of proposed trades changes the fund's and accounts' risk.

Uses risk/scenarios.json from the daily risk job: each modelled symbol's last 500 daily
moves and the current USD exposure per account. Trades are added as more exposure and
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
MAX_TRADES = 500


def ticker(symbol) -> str:
    """One spelling everywhere: IBKR's "BRK B" is LSEG's and the risk data's "BRK.B"."""
    return str(symbol or "").strip().upper().replace(" ", ".")


def reject_reason(symbol: str, quantity: float, account: str, data: dict) -> str:
    if not math.isfinite(quantity) or not quantity:
        return "quantity must be a non-zero number"
    if symbol not in data["returns"]:
        return "not in the risk model: no or under 250 days of LSEG price history, or an option/warrant"
    if account and account not in data["nlv"]:
        return f"unknown account {account}"
    return ""


def tail_days(pnl: list[float]) -> list[int]:
    """The worst 2.5% of days, used to split expected shortfall across trades."""
    return sorted(range(len(pnl)), key=pnl.__getitem__)[:math.ceil(0.025 * len(pnl))]


def what_if(data: dict, sectors: dict[str, str], trades: list[dict]) -> dict:
    """trades: [{"symbol", "quantity", "account" (optional)}]. Rows that cannot be modelled are
    returned under "rejected" with a reason; raises ValueError if none can be modelled."""
    returns, nlv = data["returns"], data["nlv"]
    days = len(data["dates"])
    market = returns.get(data.get("market", ""))
    accepted, rejected = [], []
    for row, trade in enumerate(trades[:MAX_TRADES], start=1):
        symbol = ticker(trade.get("symbol"))
        account = str(trade.get("account") or "").strip()
        try:
            quantity = float(trade.get("quantity"))
        except (TypeError, ValueError):
            quantity = 0.0
        reason = reject_reason(symbol, quantity, account, data)
        if reason:
            rejected.append({"row": row, "symbol": symbol, "reason": reason})
            continue
        price, fx = data["last_close"][symbol], data.get("fx_to_usd", {}).get(symbol, 1.0)
        value = quantity * price * fx  # listing currency -> USD with IBKR's rate
        accepted.append({"row": row, "symbol": symbol, "quantity": quantity, "account": account or None,
                         "price": price, "fx_to_usd": fx, "value": value,
                         "sector": sectors.get(symbol, "Unclassified"),
                         "pnl": book_pnl({symbol: value}, returns, days)})
    if not accepted:
        raise ValueError("None of the trades can be modelled: "
                         + "; ".join(f"row {r['row']} {r['symbol'] or '(blank)'}: {r['reason']}" for r in rejected[:5]))

    def add(book: dict[str, float], legs: list[dict]) -> dict[str, float]:
        out = dict(book)
        for leg in legs:
            out[leg["symbol"]] = out.get(leg["symbol"], 0.0) + leg["value"]
        return out

    def basket_pnl(legs: list[dict]) -> list[float]:
        return [sum(day) for day in zip(*(leg["pnl"] for leg in legs))]

    fund_book: dict[str, float] = {}
    for holdings in data["exposure"].values():
        fund_book = add(fund_book, [{"symbol": s, "value": v} for s, v in holdings.items()])
    fund_before = book_pnl(fund_book, returns, days)
    basket = basket_pnl(accepted)
    fund_after = [b + t for b, t in zip(fund_before, basket)]
    tail = tail_days(fund_after)

    accounts = []
    for account in sorted({leg["account"] for leg in accepted if leg["account"]}):
        legs = [leg for leg in accepted if leg["account"] == account]
        before = book_pnl(data["exposure"].get(account, {}), returns, days)
        after = [b + t for b, t in zip(before, basket_pnl(legs))]
        accounts.append({"id": account, "trades": len(legs), "before": risk(before, nlv[account], market),
                         "after": risk(after, nlv[account], market)})

    g_before, g_after = gross_by_sector(fund_book, sectors), gross_by_sector(add(fund_book, accepted), sectors)
    total_before, total_after = sum(g_before.values()) or 1, sum(g_after.values()) or 1
    # Only sectors the trades touch: the rest keep their dollars and just shrink in share as the book grows.
    traded = {leg["sector"] for leg in accepted}
    sector_rows = [{"name": name, "before_usd": round(g_before.get(name, 0.0), 2), "after_usd": round(g_after.get(name, 0.0), 2),
                    "before_pct": round(g_before.get(name, 0.0) / total_before * 100, 2),
                    "after_pct": round(g_after.get(name, 0.0) / total_after * 100, 2)}
                   for name in sorted(traded)]
    worst = sorted(range(days), key=fund_before.__getitem__)[:5]
    return {
        "as_of": data.get("as_of"), "scenarios": days,
        "trades": [{**{k: v for k, v in leg.items() if k != "pnl"}, "price": round(leg["price"], 4),
                    "value": round(leg["value"], 2),
                    "stand_alone_var_95": round(var_hist(leg["pnl"], 0.05), 2),
                    "es_contribution": round(-sum(leg["pnl"][i] for i in tail) / len(tail), 2)}
                   for leg in accepted],
        "rejected": rejected + [{"row": row, "symbol": str(t.get("symbol", "")), "reason": f"over the {MAX_TRADES}-trade limit"}
                                for row, t in enumerate(trades[MAX_TRADES:], start=MAX_TRADES + 1)],
        "basket": {"net_value": round(sum(leg["value"] for leg in accepted), 2),
                   "gross_value": round(sum(abs(leg["value"]) for leg in accepted), 2),
                   "stand_alone_var_95": round(var_hist(basket, 0.05), 2),
                   "es_contribution": round(-sum(basket[i] for i in tail) / len(tail), 2)},
        "fund": {"before": risk(fund_before, sum(nlv.values()) + 0.0, market),
                 "after": risk(fund_after, sum(nlv.values()) + 0.0, market)},
        "accounts": accounts,
        "sectors": sector_rows,
        "gross_before": round(total_before, 2), "gross_after": round(total_after, 2),
        "worst_days": [{"date": data["dates"][i], "before": round(fund_before[i], 2), "trades": round(basket[i], 2),
                        "after": round(fund_after[i], 2)} for i in worst],
    }
