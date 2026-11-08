"""Run in the risk venv:  risk-venv/bin/python -m unittest risk/test_risk_job.py"""
import sys
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
from risk_job import build_report, scenario_data  # noqa: E402
sys.path.insert(0, str(Path(__file__).parents[1]))
from whatif import what_if  # noqa: E402

DAYS = pd.bdate_range("2024-01-01", periods=301).strftime("%Y-%m-%d")


def prices(series: dict[str, list[float]]) -> pd.DataFrame:
    return pd.DataFrame([{"date": d, "symbol": s, "close": p}
                         for s, closes in series.items() for d, p in zip(DAYS[-len(closes):], closes)])


def path(returns) -> list[float]:
    return list(100 * np.cumprod(np.r_[1.0, 1 + np.asarray(returns)]))


def position(symbol, value, account="U1", category="STK", fx="1"):
    return {"account_id": account, "symbol": symbol, "asset_category": category,
            "market_value": str(value), "fx_rate_to_base": fx}


rng = np.random.default_rng(7)
A = rng.normal(0, 0.02, 300)
B = rng.normal(0, 0.01, 300)
MARKET = rng.normal(0, 0.01, 300)
PRICES = pd.concat([
    prices({"AAA": path(A), "BBB": path(B), "IVV": path(MARKET), "NEW": path(rng.normal(0, 0.01, 99))}),
    # Delisted: 260 closes that stop 41 business days before the latest date.
    pd.DataFrame({"date": DAYS[:260], "symbol": "DEAD", "close": path(A)[:260]}),
])


class RiskJobTests(unittest.TestCase):
    def setUp(self):
        report = {"date": "20261106", "cash": {"U1": "1000", "U2": "0"}, "positions": [
            position("AAA", 10_000), position("BBB", -5_000, account="U2"),
            position("BBB", 400_000, fx="0.01", account="U2"),       # ¥400,000 = $4,000
            position("AAA 261120C00010000", 500, category="OPT"),
            position("NEW", 700), position("NOPE", 300), position("DEAD", 200)]}
        self.report = report
        self.result = build_report(report, PRICES, {"AAA": "Tech"})

    def test_var_matches_sorted_scenario_losses(self):
        pnl = 10_000 * A + (-5_000 + 4_000) * B
        losses = np.sort(pnl)
        self.assertAlmostEqual(self.result["fund"]["var_95"], round(-losses[int(np.ceil(0.05 * 300)) - 1], 2))
        # Rockafellar-Uryasev ES: VaR plus the average excess loss over a 2.5% tail (7.5 of 300 days).
        var = -losses[int(np.ceil(0.025 * 300)) - 1]
        es = var + np.maximum(-pnl - var, 0).sum() / (0.025 * 300)
        self.assertAlmostEqual(self.result["fund"]["es_97_5"], round(es, 2), places=1)

    def test_contributions_add_up_to_expected_shortfall(self):
        total = sum(s["es_contribution"] for s in self.result["sectors"])
        self.assertAlmostEqual(total, self.result["fund"]["es_97_5"], delta=1.0)
        self.assertEqual({s["sector"] for s in self.result["sectors"]}, {"Tech", "Unclassified"})

    def test_nlv_includes_cash_and_every_position(self):
        # 10,000 - 5,000 + 4,000 + 500 + 700 + 300 + 200 + 1,000 cash
        self.assertEqual(self.result["fund"]["nlv"], 11_700)
        self.assertEqual({a["id"] for a in self.result["accounts"]}, {"U1", "U2"})

    def test_unmodellable_positions_are_listed_with_reasons(self):
        reasons = {x["symbol"]: x["reason"] for x in self.result["coverage"]["excluded"]}
        self.assertEqual(reasons["AAA 261120C00010000"], "options/warrants are not modelled yet")
        self.assertEqual(reasons["NOPE"], "no LSEG price history")
        self.assertEqual(reasons["NEW"], "only 100 days of price history")
        self.assertTrue(reasons["DEAD"].startswith("no LSEG price since"))

    def test_market_sensitivity_and_worst_days(self):
        self.assertIsNotNone(self.result["fund"]["sp500_down_1pct"])
        worst = [d["pnl"] for d in self.result["worst_days"]]
        self.assertEqual(worst, sorted(worst))
        self.assertEqual(len(worst), 5)


    def test_scenario_data_reproduces_the_report_for_the_what_if(self):
        data = scenario_data(self.report, PRICES)
        before = what_if(data, {}, "AAA", 1)["fund"]["before"]
        fund = self.result["fund"]
        for key in ("var_95", "var_99", "es_97_5", "sp500_down_1pct"):
            self.assertAlmostEqual(before[key], fund[key], delta=1.0)  # returns stored to 6 decimals
        self.assertEqual(data["exposure"]["U2"], {"BBB": -1000.0})  # -5,000 + ¥400,000 at 0.01
        self.assertEqual(data["fx_to_usd"], {"BBB": 0.01})


if __name__ == "__main__":
    unittest.main()
