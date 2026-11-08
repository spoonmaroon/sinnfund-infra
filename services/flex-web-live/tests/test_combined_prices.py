import gzip
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from parse_flex_positions_live import parse_archives
from portfolio_api import portfolio_summary, price_validation_summary


XML = b"""<FlexQueryResponse><FlexStatements><FlexStatement accountId="U1" fromDate="20261105" toDate="20261105">
<OpenPositions><OpenPosition levelOfDetail="SUMMARY" reportDate="20261105"
 accountId="U1" conid="1" symbol="TEST" description="Test Corp" assetCategory="STK"
 currency="USD" side="Long" position="2" multiplier="1" markPrice="100"
 positionValue="200" costBasisPrice="80" costBasisMoney="160"
 fifoPnlUnrealized="40" percentOfNAV="10" listingExchange="NASDAQ"
 isin="US0000000001" figi="BBG000000001"/></OpenPositions>
</FlexStatement></FlexStatements></FlexQueryResponse>"""


class CombinedPriceTests(unittest.TestCase):
    def test_keeps_close_and_delayed_marks_in_one_row(self):
        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = {
            "snapshot_age_seconds": 10,
            "connected": True,
            "quotes": {
                "TEST": {
                    "price": 110,
                    "ric": "/TEST.O",
                    "bid": 109,
                    "ask": 111,
                    "trade_time": "20:45:00",
                    "snapshot_at": "2026-11-05T21:00:00+00:00",
                    "last_stream_event_at": "2026-11-05T20:59:58+00:00",
                }
            },
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "main.xml.gz"
            with gzip.open(path, "wb") as stream:
                stream.write(XML)
            with patch("parse_flex_positions_live.requests.get", return_value=response):
                positions, _ = parse_archives([path])

        row = positions[0]
        self.assertEqual(row["close_mark_price"], "100")
        self.assertEqual(row["delayed_mark_price"], "110")
        self.assertEqual(row["mark_price"], "110")
        self.assertEqual(row["close_market_value"], "200")
        self.assertEqual(row["delayed_market_value"], "220.00000000")
        self.assertEqual(row["price_source"], "lseg_delayed")
        self.assertEqual(row["price_validation"], "accepted")

        report = {"date": "20261105", "positions": positions, "lots": []}
        summary = portfolio_summary(report)
        validation = price_validation_summary(report)
        self.assertEqual(summary["totals"]["close_position_value"], 200.0)
        self.assertEqual(summary["totals"]["position_value"], 220.0)
        self.assertEqual(validation["coverage_pct"], 100.0)
        self.assertEqual(validation["absolute_price_gap_pct"]["max"], 10.0)


if __name__ == "__main__":
    unittest.main()
