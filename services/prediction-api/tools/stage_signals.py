#!/usr/bin/env python3
"""Stage the signal book in TWS at trade time: live prices, long/short decided now, account rebalanced.

Run on the machine with TWS (or IB Gateway) open. Python 3.11 + ib_insync (pip install ib_insync tzdata).

    python stage_signals.py --account U1234567 --port 7496                 # live TWS, plan + confirm + stage
    python stage_signals.py --account DU1234567 --port 7497 --dry-run      # paper, plan only
    python stage_signals.py --account U1234567 --order-type LMT --limit-bps 10 --tif GTC

Steps (ADR-0034):
 1. Pending LIVE signals from the public signal API (or --signals FILE, the same JSON).
 2. Live price per symbol from TWS: bid/ask midpoint, else last trade. No price -> left out and listed.
 3. Long if the signal's target is above the live price, short if below; each signal is +-gross/N over
    all N signals (an unpriced signal's share stays cash); signals on one stock are added.
 4. Rebalance against the account's current holdings and NetLiquidation. By default only signal symbols
    are traded and other holdings are left alone; --close-others also closes everything else
    (what the class Implementation tool does).
 5. Print the plan, ask for "yes", stage every order with transmit=False. A person transmits in TWS.
Nothing here sends an order to the market.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
from pathlib import Path
import sys
import time
import urllib.request

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # signal_weights.py, shared with the website CSV
from signal_weights import signal_targets  # noqa: E402

PENDING_URL = "https://sinnfund.jkoh.dev/prediction/signals/pending"
ORDER_TYPES = ("ADAPTIVE", "MKT", "LMT", "MIDPRICE")


# ---------- decisions (pure; tested without TWS) ----------

def plan_trades(targets: dict[str, float], held: dict[str, float], nlv: float,
                prices: dict[str, float], close_others: bool = False) -> list[dict]:
    """Whole-share orders that move each symbol from what is held to target x NetLiquidation."""
    symbols = set(targets) | (set(held) if close_others else set())
    trades = []
    for symbol in sorted(symbols):
        price = prices.get(symbol)
        if not price:
            continue  # reported by the caller as unpriced
        want = round(targets.get(symbol, 0.0) * nlv / price)
        have = held.get(symbol, 0.0)
        delta = want - have
        if abs(delta) >= 1:
            trades.append({"symbol": symbol, "action": "BUY" if delta > 0 else "SELL",
                           "quantity": int(abs(delta)), "held": have, "target_shares": want,
                           "target": targets.get(symbol, 0.0), "price": price})
    return trades


def order_fields(trade: dict, order_type: str, tif: str, limit_bps: float, account: str) -> dict:
    """ib_insync Order(...) keyword arguments; always transmit=False (staged, not sent)."""
    fields = {"action": trade["action"], "totalQuantity": trade["quantity"], "account": account,
              "tif": tif, "transmit": False, "outsideRth": False}
    if order_type == "ADAPTIVE":
        return {**fields, "orderType": "MKT", "algoStrategy": "Adaptive",
                "algoParams": [("adaptivePriority", "Normal")]}
    if order_type == "LMT":
        sign = 1 if trade["action"] == "BUY" else -1  # pay up to limit_bps above, or sell down to below
        return {**fields, "orderType": "LMT", "lmtPrice": round(trade["price"] * (1 + sign * limit_bps / 10_000), 2)}
    return {**fields, "orderType": order_type}  # MKT or MIDPRICE


def live_price(ticker) -> float | None:
    """Bid/ask midpoint, else last trade. Never the close field. Before the open with no quotes, the last
    trade is the previous session's: run during market hours for live prices."""
    def ok(x):
        return x is not None and not math.isnan(x) and x > 0
    if ok(ticker.bid) and ok(ticker.ask):
        return (ticker.bid + ticker.ask) / 2
    return ticker.last if ok(ticker.last) else None


# ---------- TWS (ib_insync) ----------

def fetch_signals(path: str | None) -> list[dict]:
    if path:
        with open(path) as f:
            return json.load(f)
    with urllib.request.urlopen(PENDING_URL, timeout=30) as response:
        return json.load(response)


def tws_prices(ib, symbols: list[str], wait: float):
    """Live prices and the qualified US contracts they came from (orders go on the same contract)."""
    from ib_insync import Stock
    contracts = {c.symbol: c for c in ib.qualifyContracts(*[Stock(s, "SMART", "USD") for s in symbols])}
    ib.reqMarketDataType(1)  # live; with no subscription IB sends nothing and the symbol is left out
    tickers = {s: ib.reqMktData(c, "", False, False) for s, c in contracts.items()}
    deadline = time.time() + wait
    while time.time() < deadline and any(live_price(t) is None for t in tickers.values()):
        ib.sleep(0.25)
    prices = {s: p for s, t in tickers.items() if (p := live_price(t))}
    for c in contracts.values():
        ib.cancelMktData(c)
    return prices, contracts


def account_state(ib, account: str) -> tuple[float, dict[str, float]]:
    nlv = next((float(v.value) for v in ib.accountSummary(account)
                if v.tag == "NetLiquidation" and v.currency in ("USD", "BASE")), None)
    if not nlv:
        raise SystemExit(f"No NetLiquidation for {account}: is it an account this TWS login manages?")
    # US-dollar stocks only: a same-ticker listing elsewhere (SAP on Xetra) is a different holding.
    held = {p.contract.symbol: float(p.position) for p in ib.positions(account)
            if p.contract.secType == "STK" and p.contract.currency == "USD" and p.position}
    return nlv, held


def write_plan(path: str, trades: list[dict]) -> None:
    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["symbol", "action", "quantity", "held", "target_shares", "target", "price"])
        writer.writeheader()
        writer.writerows(trades)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--account", required=True)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=7497, help="7496 live TWS, 7497 paper TWS")
    parser.add_argument("--client", type=int, default=7)
    parser.add_argument("--signals", help="JSON file shaped like the API's /signals/pending (default: fetch it)")
    parser.add_argument("--gross", type=float, default=1.0, help="sum of |weights| (1.0 = 100%% of NetLiquidation)")
    parser.add_argument("--close-others", action="store_true", help="also close holdings that are not signals")
    parser.add_argument("--order-type", choices=ORDER_TYPES, default="ADAPTIVE")
    parser.add_argument("--tif", choices=("DAY", "GTC"), default="DAY")
    parser.add_argument("--limit-bps", type=float, default=10.0, help="LMT only: how far past the live price")
    parser.add_argument("--price-wait", type=float, default=8.0, help="seconds to wait for live quotes")
    parser.add_argument("--dry-run", action="store_true", help="print and save the plan; stage nothing")
    args = parser.parse_args(argv)
    if not 0 < args.gross <= 1:
        parser.error("--gross must be above 0 and at most 1")

    signals = fetch_signals(args.signals)
    if not signals:
        print("No pending LIVE signals.")
        return 0

    from ib_insync import IB, Order, TagValue
    ib = IB()
    ib.connect(args.host, args.port, clientId=args.client)
    try:
        nlv, held = account_state(ib, args.account)
        symbols = sorted({s["symbol"] for s in signals} | (set(held) if args.close_others else set()))
        prices, contracts = tws_prices(ib, symbols, args.price_wait)
        targets, left_out = signal_targets(signals, prices, args.gross)
        trades = plan_trades(targets, held, nlv, prices, args.close_others)

        print(f"\nAccount {args.account}: NetLiquidation ${nlv:,.0f}; {len(signals)} signals, "
              f"{sum(t > 0 for t in targets.values())} long, {sum(t < 0 for t in targets.values())} short")
        for s in left_out:
            print(f"  left out: {s['symbol']} ({s['analyst']} {s['signal_id']}): no live price or target equals price")
        if args.close_others and (unpriced := sorted(set(held) - set(prices))):
            print(f"  NOT closed (no live price): {', '.join(unpriced)}")
        for t in trades:
            print(f"  {t['action']:4} {t['quantity']:>8} {t['symbol']:8} @~{t['price']:.2f}  "
                  f"held {t['held']:g} -> {t['target_shares']:g}  ({t['target']:+.4f})")
        plan_path = f"signal_plan_{time.strftime('%Y%m%d_%H%M%S')}.csv"
        write_plan(plan_path, trades)
        print(f"\n{len(trades)} orders, {args.order_type} {args.tif}; plan saved to {plan_path}")
        if args.dry_run or not trades:
            return 0
        if input("Stage these orders in TWS (not transmitted)? Type yes: ").strip().lower() != "yes":
            print("Nothing staged.")
            return 0
        for t in trades:
            fields = order_fields(t, args.order_type, args.tif, args.limit_bps, args.account)
            fields["algoParams"] = [TagValue(k, v) for k, v in fields.get("algoParams", [])]
            ib.placeOrder(contracts[t["symbol"]], Order(**fields))
        ib.sleep(1)
        print(f"Staged {len(trades)} orders as Not Transmitted. Review and transmit them in TWS.")
        return 0
    finally:
        ib.disconnect()


if __name__ == "__main__":
    sys.exit(main())
