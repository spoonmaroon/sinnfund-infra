"""Hypothetical trades: when a new daily Flex file changes positions, the API serves the change.

Builds real gzipped Flex archives in a temp folder, serves them over HTTP, then
buys, sells and resizes positions day by day.
"""
import gzip
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
from urllib.request import urlopen
from xml.sax.saxutils import quoteattr

import uvicorn

SERVICE = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("web_live_changes", SERVICE / "web-live.py")
web_live = importlib.util.module_from_spec(spec)
spec.loader.exec_module(web_live)


def attrs(**values):
    return " ".join(f"{key}={quoteattr(str(value))}" for key, value in values.items())


def write_day(root: Path, date: str, holdings: dict, trades=(), cash="1000", deposit="0", folder=None):
    """holdings: {symbol: (quantity, price)}; trades: [(transaction_id, symbol, side, quantity, price)]."""
    rows = "".join(
        f"<OpenPosition {attrs(accountId='U1', conid=symbol, symbol=symbol, description=symbol, assetCategory='STK', currency='USD', fxRateToBase=1, side='Long', position=qty, multiplier=1, markPrice=price, positionValue=qty * price, costBasisMoney=qty * 10, fifoPnlUnrealized=qty * (price - 10), reportDate=date, levelOfDetail='SUMMARY')}/>"
        for symbol, (qty, price) in holdings.items())
    fills = "".join(
        f"<Trade {attrs(accountId='U1', tradeDate=date, dateTime=f'{date};1000{n}', symbol=symbol, description=symbol, assetCategory='STK', buySell=side, quantity=qty, tradePrice=price, currency='USD', fxRateToBase=1, proceeds=-qty * price, ibCommission=-1, fifoPnlRealized=0, openCloseIndicator='O', transactionID=tid, levelOfDetail='EXECUTION')}/>"
        for n, (tid, symbol, side, qty, price) in enumerate(trades))
    xml = (f"<FlexQueryResponse><FlexStatements><FlexStatement accountId='U1' fromDate='{date}' toDate='{date}'>"
           f"<OpenPositions>{rows}</OpenPositions><Trades>{fills}</Trades>"
           f"<CashReport><CashReportCurrency currency='BASE_SUMMARY' endingCash='{cash}' depositWithdrawals='{deposit}' accountTransfers='0' internalTransfers='0'/></CashReport>"
           "</FlexStatement></FlexStatements></FlexQueryResponse>")
    path = root / date / "main.xml.gz" if folder is None else root / folder
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(gzip.compress(xml.encode()))


class PositionChangeTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        for target, value in [("ARCHIVE_DIR", self.root), ("reprice", lambda positions, lots: None),
                              ("get_lseg_last_snapshot_at", lambda: None), ("YTD_DIR", self.root / "ytd"),
                              ("sp500_ytd", lambda year: None)]:
            patcher = patch.object(web_live, target, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        # Every request re-reads the archive, as the 30 s cache does once it expires.
        for cache in (web_live.report_cache, web_live.trades_cache, web_live.ytd_cache):
            patcher = patch.object(cache, "ttl_seconds", 0)
            patcher.start()
            self.addCleanup(patcher.stop)
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        self.base = f"http://127.0.0.1:{port}"
        self.server = uvicorn.Server(uvicorn.Config(web_live.app, port=port, log_level="error"))
        threading.Thread(target=self.server.run, daemon=True).start()
        self.addCleanup(setattr, self.server, "should_exit", True)
        while not self.server.started:
            time.sleep(0.01)

    def get(self, path):
        with urlopen(self.base + path) as response:
            return json.loads(response.read())

    def holdings(self):
        report = self.get("/api/v1/report")
        return report["date"], {row["symbol"]: (float(row["quantity"]), row["day_change_value"])
                                for row in report["positions"]}

    def test_buys_sells_and_resizes_show_up_on_the_next_request(self):
        write_day(self.root, "20261102", {"AAA": (10, 10)})
        self.assertEqual(self.holdings(), ("20261102", {"AAA": (10.0, "")}))

        # Sell all AAA, buy BBB.
        write_day(self.root, "20261103", {"BBB": (5, 20)}, cash="1100")
        date, held = self.holdings()
        self.assertEqual((date, held), ("20261103", {"BBB": (5.0, "")}))  # new position: no previous close
        totals = self.get("/api/v1/portfolio")["totals"]
        self.assertEqual(totals["position_value"], 100.0)
        self.assertEqual(totals["net_liquidation_value"], 1200.0)

        # Add to BBB and the price moves 20 -> 22: day change is the move on the new size.
        write_day(self.root, "20261104", {"BBB": (8, 22)})
        self.assertEqual(self.holdings(), ("20261104", {"BBB": (8.0, "16.00")}))

    def test_trade_log_is_hidden_until_enabled(self):
        write_day(self.root, "20261102", {"AAA": (10, 10)}, trades=[("T1", "AAA", "BUY", 10, 10)])
        write_day(self.root, "20261103", {"BBB": (5, 20)},
                  trades=[("T2", "AAA", "SELL", -10, 12), ("T3", "BBB", "BUY", 5, 20)])
        with self.assertRaises(HTTPError) as error:
            urlopen(self.base + "/api/v1/trades")
        self.assertEqual(error.exception.code, 404)
        error.exception.close()
        self.assertNotIn("/api/v1/trades", self.get("/api/v1")["endpoints"])

        with patch.object(web_live, "TRADE_LOG", True):
            trades = self.get("/api/v1/trades")["trades"]
        self.assertEqual([(t["transaction_id"], t["buy_sell"], t["symbol"]) for t in trades],
                         [("T3", "BUY", "BBB"), ("T2", "SELL", "AAA"), ("T1", "BUY", "AAA")])

    def test_a_re_downloaded_day_does_not_double_count_trades(self):
        write_day(self.root, "20261102", {"AAA": (10, 10)}, trades=[("T1", "AAA", "BUY", 10, 10)])
        write_day(self.root, "20261103", {"AAA": (10, 10)}, trades=[("T1", "AAA", "BUY", 10, 10)])
        with patch.object(web_live, "TRADE_LOG", True):
            self.assertEqual(len(self.get("/api/v1/trades")["trades"]), 1)


    def test_ytd_pnl_excludes_money_moved_in(self):
        ytd = self.root / "ytd"
        write_day(ytd, "20251231", {"AAA": (10, 10)}, cash="900", folder="2026/start.xml.gz")  # $1,000 NLV
        write_day(ytd, "20261008", {"AAA": (10, 10)}, deposit="500", folder="2026/flows.xml.gz")  # +$500 put in
        write_day(self.root, "20261102", {"AAA": (10, 12)}, cash="1500")   # $120 + $1,500 = $1,620
        write_day(self.root, "20261103", {"AAA": (10, 13)}, cash="1500", deposit="0")  # $1,630
        ytd_result = self.get("/api/v1/ytd")
        self.assertEqual(ytd_result["start_nlv"], 1000.0)
        self.assertEqual(ytd_result["net_flows"], 500.0)
        self.assertEqual(ytd_result["pnl"], 130.0)  # 1,630 - 1,000 - 500
        self.assertEqual(ytd_result["pnl_pct"], 10.4)  # 130 / (1,000 + 500/2)

    def test_ytd_counts_a_deposit_once_when_its_day_is_downloaded_again(self):
        ytd = self.root / "ytd"
        write_day(ytd, "20251231", {"AAA": (10, 10)}, cash="900", folder="2026/start.xml.gz")
        write_day(ytd, "20261008", {"AAA": (10, 10)}, folder="2026/flows.xml.gz")
        write_day(self.root, "20261102", {"AAA": (10, 10)}, cash="1400", deposit="500")
        # flex-ingest --date 20261103 --force stores the 20261102 statement again under 20261103.
        write_day(self.root, "20261102", {"AAA": (10, 10)}, cash="1400", deposit="500", folder="20261103/main.xml.gz")
        self.assertEqual(self.get("/api/v1/ytd")["net_flows"], 500.0)

if __name__ == "__main__":
    unittest.main()
