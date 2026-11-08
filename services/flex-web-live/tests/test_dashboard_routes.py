import gzip
import importlib.util
import json
import socket
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import uvicorn

SERVICE = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("web_live", SERVICE / "web-live.py")
web_live = importlib.util.module_from_spec(spec)
spec.loader.exec_module(web_live)

REPORT = {"date": "20261106", "files": 1, "lseg_last_snapshot_at": None, "carried_forward": {}, "cash": {"U1": "50"},
          "positions": [{"account_id": "U1", "symbol": "TEST", "market_value": "200"}],
          "lots": [{"account_id": "U1", "symbol": "TEST"}]}


class DashboardRouteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.patcher = patch.object(web_live.report_cache, "get", return_value=REPORT)
        cls.patcher.start()
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        cls.base = f"http://127.0.0.1:{port}"
        cls.server = uvicorn.Server(uvicorn.Config(web_live.app, port=port, log_level="error"))
        threading.Thread(target=cls.server.run, daemon=True).start()
        while not cls.server.started:
            time.sleep(0.01)

    @classmethod
    def tearDownClass(cls):
        cls.server.should_exit = True
        cls.patcher.stop()

    def get(self, path):
        with urlopen(self.base + path) as response:
            return response.status, response.read().decode()

    def test_ui_and_assets_are_served_with_relative_api_calls(self):
        status, page = self.get("/")
        self.assertEqual(status, 200)
        self.assertIn('src="static/app.js"', page)
        status, script = self.get("/static/app.js")
        self.assertEqual(status, 200)
        self.assertIn('fetch("api/v1/report")', script)
        self.assertNotIn('fetch("/', script)  # absolute paths would escape nginx's /live/ prefix
        self.assertEqual(self.get("/static/app.css")[0], 200)

    def test_report_and_sectors_endpoints(self):
        report = json.loads(self.get("/api/v1/report")[1])
        self.assertEqual(report["positions"][0]["symbol"], "TEST")
        self.assertEqual(len(report["lots"]), 1)
        self.assertIn("sectors", json.loads(self.get("/api/v1/sectors")[1]))

    def test_json_is_encoded_once_and_gzipped_when_accepted(self):
        plain = json.loads(self.get("/api/v1/report")[1])
        with urlopen(Request(self.base + "/api/v1/report", headers={"Accept-Encoding": "gzip"})) as response:
            self.assertEqual(response.headers["Content-Encoding"], "gzip")
            self.assertEqual(json.loads(gzip.decompress(response.read())), plain)
        self.assertIs(web_live._encoded["report"][0], REPORT)

    def test_risk_reports_unavailable_until_the_job_runs(self):
        with patch.object(web_live, "RISK_FILE", SERVICE / "no-such-risk.json"):
            self.assertEqual(json.loads(self.get("/api/v1/risk")[1])["available"], False)

    def test_api_stays_read_only(self):
        for path in ("/", "/api/v1/report", "/api/v1/sectors"):
            with self.assertRaises(HTTPError) as error:
                urlopen(Request(self.base + path, method="POST"))
            self.assertEqual(error.exception.code, 405)


class NavDetectionTests(unittest.TestCase):
    def test_history_is_off_until_nav_totals_arrive(self):
        import gzip, tempfile
        with tempfile.TemporaryDirectory() as root:
            day = Path(root) / "20261106"
            day.mkdir()
            archive = day / "main.xml.gz"
            with patch.object(web_live, "ARCHIVE_DIR", Path(root)):
                archive.write_bytes(gzip.compress(b'<EquitySummaryByReportDateInBase accountId="U1" />'))
                self.assertFalse(web_live.nav_available("20261106"))
                self.assertFalse(web_live.load_history()["available"])
                archive.write_bytes(gzip.compress(
                    b'<EquitySummaryByReportDateInBase accountId="U1" reportDate="20261106" total="100" />'))
                self.assertTrue(web_live.nav_available("20261106"))


if __name__ == "__main__":
    unittest.main()
