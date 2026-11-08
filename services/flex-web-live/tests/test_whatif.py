"""Pre-trade what-if: math for a basket of trades, and the API route."""
import importlib.util
import json
import socket
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import uvicorn

from whatif import es_hist, var_hist, what_if

SERVICE = Path(__file__).resolve().parents[1]

# 40 days: AAA and the market move together, HEDGE moves against AAA.
AAA = [(-1) ** i * 0.01 * (1 + i % 7) for i in range(40)]
DATA = {
    "as_of": "20261106", "market": "IVV", "dates": [f"2026-01-{i + 1:02d}" for i in range(40)],
    "returns": {"AAA": AAA, "HEDGE": [-r for r in AAA], "IVV": [r / 2 for r in AAA], "JPY": AAA},
    "last_close": {"AAA": 10.0, "HEDGE": 20.0, "IVV": 500.0, "JPY": 1000.0},
    "fx_to_usd": {"JPY": 0.01},
    "exposure": {"U1": {"AAA": 1000.0}, "U2": {"AAA": 500.0}},
    "nlv": {"U1": 2000.0, "U2": 1000.0},
}
SECTORS = {"AAA": "Tech", "HEDGE": "Funds"}


class MathTests(unittest.TestCase):
    def test_var_and_es_follow_riskfolio_definitions(self):
        pnl = [float(x) for x in range(-20, 20)]  # 40 days, worst is -20
        self.assertEqual(var_hist(pnl, 0.05), 19.0)  # ceil(0.05*40)=2nd worst
        # Rockafellar-Uryasev: VaR + mean excess beyond VaR / alpha
        self.assertAlmostEqual(es_hist(pnl, 0.025), 20.0)

    def test_before_is_the_current_book_and_after_adds_the_trade(self):
        result = what_if(DATA, SECTORS, [{"symbol": "aaa", "quantity": 50}])
        before = [1500 * r for r in AAA]
        self.assertAlmostEqual(result["fund"]["before"]["var_95"], round(var_hist(before, 0.05), 2))
        after = [2000 * r for r in AAA]  # 50 x $10 more AAA
        self.assertAlmostEqual(result["fund"]["after"]["var_95"], round(var_hist(after, 0.05), 2))
        self.assertEqual(result["trades"][0]["value"], 500.0)
        self.assertEqual(result["sectors"], [])  # all exposure stays in Tech: no weight changes
        self.assertEqual(result["accounts"], [])  # no account given: fund only

    def test_a_hedge_lowers_risk_and_has_negative_es_contribution(self):
        result = what_if(DATA, SECTORS, [{"symbol": "HEDGE", "quantity": 50, "account": "U1"}])  # offsets U1 fully
        self.assertEqual(result["accounts"][0]["after"]["var_95"], 0.0)
        self.assertLess(result["fund"]["after"]["es_97_5"], result["fund"]["before"]["es_97_5"])
        self.assertLessEqual(result["trades"][0]["es_contribution"], 0)
        day = result["worst_days"][0]
        self.assertAlmostEqual(day["before"] + day["trades"], day["after"], places=1)

    def test_basket_adds_up_and_bad_rows_are_listed_not_fatal(self):
        result = what_if(DATA, SECTORS, [
            {"symbol": "AAA", "quantity": 50, "account": "U1"}, {"symbol": "HEDGE", "quantity": -10, "account": "U2"},
            {"symbol": "ZZZ", "quantity": 1}, {"symbol": "AAA", "quantity": "abc"}, {"symbol": "AAA", "quantity": 1, "account": "U9"}])
        self.assertEqual([t["row"] for t in result["trades"]], [1, 2])
        self.assertEqual([(r["row"], r["reason"][:14]) for r in result["rejected"]],
                         [(3, "not in the ris"), (4, "quantity must "), (5, "unknown accoun")])
        self.assertEqual(result["basket"]["net_value"], 300.0)  # 500 + (-10 x $20)
        self.assertAlmostEqual(sum(t["es_contribution"] for t in result["trades"]), result["basket"]["es_contribution"], places=1)
        self.assertEqual([a["id"] for a in result["accounts"]], ["U1", "U2"])
        self.assertEqual({r["name"] for r in result["sectors"]}, {"Tech", "Funds"})

    def test_foreign_listing_is_valued_in_usd(self):
        self.assertEqual(what_if(DATA, SECTORS, [{"symbol": "JPY", "quantity": 100}])["trades"][0]["value"], 1000.0)

    def test_nothing_modellable_raises_with_the_reasons(self):
        with self.assertRaisesRegex(ValueError, "row 1 ZZZ: not in the risk model"):
            what_if(DATA, SECTORS, [{"symbol": "ZZZ", "quantity": 1}])


spec = importlib.util.spec_from_file_location("web_live_whatif", SERVICE / "web-live.py")
web_live = importlib.util.module_from_spec(spec)
spec.loader.exec_module(web_live)


class RouteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.file = Path(cls.temp.name) / "scenarios.json"
        cls.patcher = patch.object(web_live, "SCENARIOS_FILE", cls.file)
        cls.patcher.start()
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        cls.base = f"http://127.0.0.1:{port}/api/v1/risk/whatif"
        cls.server = uvicorn.Server(uvicorn.Config(web_live.app, port=port, log_level="error"))
        threading.Thread(target=cls.server.run, daemon=True).start()
        while not cls.server.started:
            time.sleep(0.01)

    @classmethod
    def tearDownClass(cls):
        cls.server.should_exit = True
        cls.patcher.stop()
        cls.temp.cleanup()

    def post(self, body):
        request = Request(self.base, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
        try:
            with urlopen(request) as response:
                return response.status, json.loads(response.read())
        except HTTPError as error:
            with error:
                return error.code, json.loads(error.read())

    def test_route(self):
        self.file.unlink(missing_ok=True)
        self.assertEqual(self.post({"trades": [{"symbol": "AAA", "quantity": 1}]})[0], 503)  # risk job has not run
        self.file.write_text(json.dumps(DATA))
        status, body = self.post({"trades": [{"symbol": "AAA", "quantity": 50, "account": "U1"},
                                             {"symbol": "ZZZ", "quantity": "1"}]})
        self.assertEqual(status, 200)
        self.assertEqual(body["trades"][0]["value"], 500.0)
        self.assertEqual(body["rejected"][0]["row"], 2)
        self.assertEqual(self.post({"trades": [{"symbol": "ZZZ", "quantity": 1}]})[0], 422)
        self.assertEqual(self.post({"trades": []})[0], 422)
        self.assertEqual(self.post({"trades": [{"symbol": "A" * 41, "quantity": 1}]})[0], 422)

if __name__ == "__main__":
    unittest.main()
