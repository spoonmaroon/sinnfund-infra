import unittest

from portfolio_api import add_day_change, carry_forward, history_series, portfolio_summary


def row(**fields):
    base = {"account_id": "U1", "conid": "1", "report_date": "20261106", "quantity": "10",
            "multiplier": "1", "fx_rate_to_base": "1", "close_mark_price": "110",
            "mark_price": "110", "delayed_mark_price": ""}
    return {**base, **fields}


PREVIOUS = [{"account_id": "U1", "conid": "1", "mark_price": "100"}]


class DayChangeTests(unittest.TestCase):
    def test_market_closed_shows_close_vs_previous_close(self):
        # Quote is from the same session as the statement (e.g. a weekend).
        position = row(delayed_mark_price="110.5", quote_last_event_at="2026-11-06T21:16:37+00:00")
        add_day_change([position], PREVIOUS)
        self.assertEqual(position["day_change_basis"], "close_vs_previous_close")
        self.assertEqual(position["day_change_price"], "10")
        self.assertEqual(position["day_change_pct"], "10.0000")
        self.assertEqual(position["day_change_value"], "100.00")

    def test_market_open_shows_delayed_vs_statement_close(self):
        position = row(delayed_mark_price="121", quote_last_event_at="2026-11-09T15:00:00+00:00")
        add_day_change([position], PREVIOUS)
        self.assertEqual(position["day_change_basis"], "delayed_vs_close")
        self.assertEqual(position["day_change_value"], "110.00")

    def test_close_only_position_still_gets_day_change(self):
        position = row()  # no LSEG quote, e.g. a foreign listing
        add_day_change([position], PREVIOUS)
        self.assertEqual(position["day_change_value"], "100.00")

    def test_carried_row_without_newer_quote_has_no_day_change(self):
        position = row(report_date="20261105", carried_from="20261105")
        add_day_change([position], [{"account_id": "U1", "conid": "1", "mark_price": "110"}])
        self.assertEqual(position["day_change_value"], "")

    def test_carried_row_with_newer_quote_uses_delayed_vs_its_close(self):
        position = row(report_date="20261105", carried_from="20261105", delayed_mark_price="112",
                       quote_last_event_at="2026-11-06T21:00:00+00:00")
        add_day_change([position], [{"account_id": "U1", "conid": "1", "mark_price": "110"}])
        self.assertEqual(position["day_change_value"], "20.00")

    def test_new_position_has_no_day_change(self):
        position = row(conid="2")
        add_day_change([position], PREVIOUS)
        self.assertEqual(position["day_change_value"], "")

    def test_foreign_values_are_converted_to_usd(self):
        position = row(currency="JPY", fx_rate_to_base="0.0063101", close_mark_price="1216",
                       mark_price="1216", quantity="500", market_value="608000",
                       unrealized_pnl="-101667.28")
        add_day_change([position], [{"account_id": "U1", "conid": "1", "mark_price": "1200"}])
        totals = portfolio_summary({"positions": [position]})["totals"]
        self.assertEqual(totals["position_value"], 3836.54)
        self.assertEqual(totals["unrealized_pnl"], -641.53)
        self.assertEqual(totals["day_change"], 50.48)


class NetLiquidationTests(unittest.TestCase):
    def test_nlv_adds_cash_and_keeps_cash_only_accounts(self):
        # A short book nets to ~0 in positions but holds the short proceeds as cash.
        positions = [row(account_id="U1", market_value="1000"),
                     row(account_id="U2", conid="2", market_value="-950")]
        summary = portfolio_summary({"positions": positions,
                                     "cash": {"U1": "0", "U2": "3950", "U3": "1000"}})
        accounts = {a["id"]: a for a in summary["accounts"]}
        self.assertEqual(accounts["U2"]["net_liquidation_value"], 3000.0)
        self.assertEqual(accounts["U3"]["net_liquidation_value"], 1000.0)  # all cash, no positions
        self.assertEqual(accounts["U2"]["allocation"], 60.0)
        self.assertEqual(summary["totals"]["net_liquidation_value"], 5000.0)
        self.assertEqual(summary["totals"]["accounts"], 3)
        self.assertEqual([a["id"] for a in summary["accounts"]], ["U2", "U1", "U3"])


class HistoryTests(unittest.TestCase):
    def test_pnl_uses_prior_quantity_and_ignores_deposits_and_new_buys(self):
        day1 = [row(mark_price="100", market_value="1000")]
        # Price +5 on 10 shares; a new position bought with fresh cash adds value, not P&L.
        day2 = [row(mark_price="105", market_value="1050"),
                row(conid="2", mark_price="50", market_value="5000", quantity="100")]
        # Yen position: 0.01 USD/JPY, +10 yen on 100 shares = $10.
        day3 = [row(mark_price="104", market_value="1040"),
                row(conid="2", mark_price="51", market_value="5100", quantity="100"),
                row(conid="3", currency="JPY", fx_rate_to_base="0.01", mark_price="1000",
                    market_value="100000", quantity="100")]
        points = history_series([("20261106", day3, {"U1"}), ("20261104", day1, {"U1"}),
                                 ("20261105", day2, {"U1"})])["points"]
        self.assertEqual([p["date"] for p in points], ["20261104", "20261105", "20261106"])
        self.assertIsNone(points[0]["day_pnl"])
        self.assertEqual(points[1]["day_pnl"], 50.0)
        self.assertEqual(points[1]["position_value"], 6050.0)
        self.assertEqual(points[2]["day_pnl"], 90.0)  # -10 + 100
        self.assertEqual(points[2]["cumulative_pnl"], 140.0)
        self.assertEqual(points[2]["position_value"], 7140.0)


    def test_account_excluded_from_a_statement_is_carried_not_dropped(self):
        day1 = [row(mark_price="100", market_value="1000"),
                row(account_id="U9", conid="9", mark_price="10", market_value="10000", quantity="1000")]
        day2 = [row(mark_price="101", market_value="1010")]  # IBKR excluded U9 this day
        day3 = [row(mark_price="101", market_value="1010"),
                row(account_id="U9", conid="9", mark_price="12", market_value="12000", quantity="1000")]
        points = history_series([("1", day1, {"U1", "U9"}), ("2", day2, {"U1"}),
                                 ("3", day3, {"U1", "U9"})])["points"]
        self.assertEqual([p["position_value"] for p in points], [11000.0, 11010.0, 13010.0])
        self.assertEqual([p["day_pnl"] for p in points], [None, 10.0, 2000.0])

    def test_closed_account_with_a_statement_is_not_carried(self):
        rows = carry_forward([row(account_id="U9")], {"U1", "U9"}, "20261105")
        self.assertEqual(rows, [])
        rows = carry_forward([row(account_id="U9")], {"U1"}, "20261105")
        self.assertEqual(rows[0]["carried_from"], "20261105")


if __name__ == "__main__":
    unittest.main()
