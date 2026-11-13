#!/usr/bin/env python3
"""Dry-run a trade CSV through the class Implementation tool (ibkr_trade_manager) without TWS.

Uses the tool's own code: read_trade_sheets -> plan_trades -> assign_buckets_and_algos -> stage_orders,
with a fake IB that records the orders instead of sending them. Needs Python 3.11 and ib_insync + tqdm
(the tool's environment.yml). Example:

    python check_trade_csv.py --tool ~/Downloads/Implementation-main signals.csv --equity 1000000

Prices and current holdings are made up (--price, default 100; --held SYMBOL=QTY), so share counts are
illustrative; the point is to see which orders the tool would stage and how (type, time in force, algo).
"""
import argparse
import sys
from collections import Counter
from pathlib import Path


class FakeIB:
    """Stands in for ib_insync.IB: records placed orders, sends nothing."""
    def __init__(self):
        self.orders = []

    def placeOrder(self, contract, order):
        self.orders.append((contract, order))
        return order


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("csv", nargs="+")
    parser.add_argument("--tool", required=True, help="folder holding ibkr_trade_manager.py and friends")
    parser.add_argument("--equity", type=float, default=1_000_000, help="pretend NetLiquidation per account")
    parser.add_argument("--price", type=float, default=100.0, help="pretend price for every symbol")
    parser.add_argument("--held", action="append", default=[], metavar="SYMBOL=QTY",
                        help="pretend current holding; anything held but not in the CSV gets closed")
    args = parser.parse_args()

    sys.path.insert(0, str(Path(args.tool).expanduser().resolve()))
    from market_data import read_trade_sheets
    from planning import assign_buckets_and_algos, plan_trades
    from execution import stage_orders

    held = {s.split("=")[0].upper(): float(s.split("=")[1]) for s in args.held}
    problems = 0
    for account, targets in read_trade_sheets(args.csv).items():  # the tool's own parser and checks
        gross = sum(abs(t) for t in targets.values())
        print(f"\n== {account}: {len(targets)} targets, net {sum(targets.values()):+.4f}, gross {gross:.4f}")
        for symbol in targets:
            if ":" in symbol or "." in symbol:
                problems += 1
                print(f"  ! {symbol}: the tool prices Stock(symbol, 'SMART', 'USD'); this will not qualify")
        if gross > 1.0001:
            print(f"  ! gross {gross:.2f} > 1: the account is levered {gross:.2f}x")
        prices = {s: args.price for s in set(targets) | set(held)}
        trades = plan_trades(targets, held, args.equity, prices)
        assign_buckets_and_algos(trades, {}, {})
        ib = FakeIB()
        stage_orders(ib, trades, account=account)
        for contract, order in ib.orders:
            algo = f" {order.algoStrategy}({','.join(p.value for p in order.algoParams)})" if order.algoStrategy else ""
            print(f"  {order.action:4} {order.totalQuantity:>10g} {contract.symbol:8} "
                  f"{order.orderType} tif={order.tif} transmit={order.transmit}{algo}")
        kinds = Counter((o.orderType, o.tif) for _, o in ib.orders)
        print(f"  {len(ib.orders)} orders staged: " + ", ".join(f"{n} {t}/{tif}" for (t, tif), n in kinds.items()))
    sys.exit(1 if problems else 0)


if __name__ == "__main__":
    main()
