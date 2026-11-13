"""tools/stage_signals.py: decisions at trade time, and a full run against a fake TWS (no ib_insync needed)."""
import json
import math
import os
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import stage_signals as stage

SIGNALS = [
    {"analyst": "max", "signal_id": "a", "symbol": "ZBH", "target_price": 120},   # above 100: long
    {"analyst": "max", "signal_id": "b", "symbol": "ZBRA", "target_price": 80},   # below 100: short
    {"analyst": "eve", "signal_id": "c", "symbol": "ZBH", "target_price": 130},   # second long on ZBH
    {"analyst": "eve", "signal_id": "d", "symbol": "KO", "target_price": 100},    # equals price: left out
    {"analyst": "bob", "signal_id": "e", "symbol": "NOPE", "target_price": 5},    # no price: left out
]
PRICES = {"ZBH": 100.0, "ZBRA": 100.0, "KO": 100.0, "MSFT": 400.0}


class DecisionTest(unittest.TestCase):
    def test_targets_are_equal_weight_netted_and_report_what_was_left_out(self):
        targets, left = stage.signal_targets(SIGNALS, PRICES)
        self.assertEqual(targets, {"ZBH": 2 / 3, "ZBRA": -1 / 3})
        self.assertEqual([s["signal_id"] for s in left], ["d", "e"])
        self.assertEqual(stage.signal_targets(SIGNALS, PRICES, gross=0.5)[0]["ZBRA"], -1 / 6)

    def test_rebalance_trades_only_signal_names_unless_asked_to_close_others(self):
        targets = {"ZBH": 0.5, "ZBRA": -0.5}
        held = {"ZBH": 1000, "ZBRA": 200, "MSFT": 30}
        trades = stage.plan_trades(targets, held, 1_000_000, PRICES)
        self.assertEqual([(t["symbol"], t["action"], t["quantity"]) for t in trades],
                         [("ZBH", "BUY", 4000), ("ZBRA", "SELL", 5200)])  # long 200 -> short 5000 in one order
        closing = stage.plan_trades(targets, held, 1_000_000, PRICES, close_others=True)
        self.assertIn(("MSFT", "SELL", 30), [(t["symbol"], t["action"], t["quantity"]) for t in closing])
        self.assertEqual(stage.plan_trades({"ZBH": 0.0001}, {"ZBH": 1}, 1_000_000, PRICES), [])  # already there

    def test_orders_are_always_staged_never_transmitted(self):
        buy = {"action": "BUY", "quantity": 10, "price": 100.0}
        sell = {**buy, "action": "SELL"}
        for kind in stage.ORDER_TYPES:
            self.assertFalse(stage.order_fields(buy, kind, "DAY", 10, "DU1")["transmit"])
        self.assertEqual(stage.order_fields(buy, "LMT", "GTC", 10, "DU1")["lmtPrice"], 100.1)
        self.assertEqual(stage.order_fields(sell, "LMT", "GTC", 10, "DU1")["lmtPrice"], 99.9)
        self.assertEqual(stage.order_fields(buy, "LMT", "GTC", 10, "DU1")["tif"], "GTC")
        self.assertEqual(stage.order_fields(buy, "ADAPTIVE", "DAY", 0, "DU1")["algoStrategy"], "Adaptive")
        self.assertEqual(stage.order_fields(buy, "MIDPRICE", "DAY", 0, "DU1")["orderType"], "MIDPRICE")

    def test_live_price_is_mid_then_last_never_close(self):
        tick = lambda bid, ask, last: types.SimpleNamespace(bid=bid, ask=ask, last=last)
        nan = math.nan
        self.assertEqual(stage.live_price(tick(99, 101, 98)), 100)
        self.assertEqual(stage.live_price(tick(nan, 101, 98)), 98)
        self.assertIsNone(stage.live_price(tick(nan, nan, nan)))
        self.assertIsNone(stage.live_price(tick(-1, -1, 0)))


class FakeIB:
    """Just enough of ib_insync.IB for stage_signals.main."""
    def __init__(self):
        self.placed = []

    def connect(self, *a, **k): pass
    def disconnect(self): pass
    def sleep(self, s): pass
    def reqMarketDataType(self, t): pass
    def cancelMktData(self, c): pass
    def qualifyContracts(self, *contracts): return [c for c in contracts if c.symbol != "NOPE"]
    def reqMktData(self, c, *a):
        p = PRICES.get(c.symbol, math.nan)
        return types.SimpleNamespace(bid=p - 0.05, ask=p + 0.05, last=p)
    def accountSummary(self, account):
        return [types.SimpleNamespace(tag="NetLiquidation", currency="USD", value="1000000")]
    def positions(self, account):
        stock = lambda s: types.SimpleNamespace(symbol=s, secType="STK")
        return [types.SimpleNamespace(contract=stock("ZBH"), position=1000.0),
                types.SimpleNamespace(contract=stock("MSFT"), position=30.0)]
    def placeOrder(self, contract, order):
        self.placed.append((contract.symbol, order))


def fake_ib_insync(ib):
    module = types.ModuleType("ib_insync")
    module.IB = lambda: ib
    module.Stock = lambda symbol, exchange, currency: types.SimpleNamespace(symbol=symbol)
    module.Order = lambda **fields: types.SimpleNamespace(**fields)
    module.TagValue = lambda tag, value: (tag, value)
    return module


class MainTest(unittest.TestCase):
    def run_main(self, *extra, answer="yes"):
        ib = FakeIB()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "signals.json"
            path.write_text(json.dumps(SIGNALS))
            cwd = os.getcwd()
            os.chdir(tmp)
            try:
                with patch.dict(sys.modules, {"ib_insync": fake_ib_insync(ib)}), \
                        patch("builtins.input", return_value=answer), patch("builtins.print"):
                    code = stage.main(["--account", "DU1", "--signals", str(path), *extra])
                plans = list(Path(tmp).glob("signal_plan_*.csv"))
            finally:
                os.chdir(cwd)
        return code, ib.placed, plans

    def test_stages_signal_orders_with_live_prices_and_leaves_other_holdings(self):
        code, placed, plans = self.run_main("--order-type", "LMT", "--tif", "GTC")
        self.assertEqual(code, 0)
        self.assertEqual(len(plans), 1)
        self.assertEqual([(s, o.action, o.totalQuantity, o.orderType, o.tif, o.transmit) for s, o in placed],
                         [("ZBH", "BUY", 5667, "LMT", "GTC", False), ("ZBRA", "SELL", 3333, "LMT", "GTC", False)])

    def test_dry_run_and_no_answer_stage_nothing(self):
        self.assertEqual(self.run_main("--dry-run")[1], [])
        self.assertEqual(self.run_main(answer="no")[1], [])

    def test_close_others_closes_holdings_outside_the_signal_book(self):
        placed = self.run_main("--close-others")[1]
        self.assertIn(("MSFT", "SELL", 30), [(s, o.action, o.totalQuantity) for s, o in placed])


if __name__ == "__main__":
    unittest.main()
