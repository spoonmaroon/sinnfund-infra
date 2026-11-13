"""/snapshot: RIC candidates, first priced venue wins, previous close as fallback, bad input refused."""
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import feed_api


class Frame:
    def __init__(self, rows):
        self.rows = rows

    def to_dict(self, orient):
        return self.rows


def fake_lseg(rows, seen):
    """Stand-in for lseg.data: a pricing stream whose snapshot is `rows`; records the RICs asked for."""
    def definition(universe, fields):
        seen.extend(universe)
        stream = types.SimpleNamespace(open=lambda with_updates: None, close=lambda: seen.append("closed"),
                                       get_snapshot=lambda: Frame(rows))
        return types.SimpleNamespace(get_stream=lambda session: stream)
    ld = types.SimpleNamespace(content=types.SimpleNamespace(pricing=types.SimpleNamespace(Definition=definition)))
    package = types.ModuleType("lseg")
    package.data = ld
    return {"lseg": package, "lseg.data": ld}


class SnapshotTest(unittest.TestCase):
    def test_rics_kept_and_plain_tickers_tried_on_each_venue(self):
        self.assertEqual(feed_api.ric_candidates("ZBH.N"), ["/ZBH.N"])
        self.assertEqual(feed_api.ric_candidates("AAPL"), ["/AAPL.N", "/AAPL.OQ", "/AAPL.P", "/AAPL.A"])

    def test_first_priced_venue_wins_and_previous_close_fills_in(self):
        rows = [{"Instrument": "/ZBH.N", "TRDPRC_1": 107.5, "HST_CLOSE": 107.0},
                {"Instrument": "/AAPL.N", "TRDPRC_1": None, "HST_CLOSE": None},
                {"Instrument": "/AAPL.OQ", "TRDPRC_1": float("nan"), "HST_CLOSE": 280.0},
                {"Instrument": "/AAPL.P", "TRDPRC_1": 281.0, "HST_CLOSE": 280.0}]
        seen = []
        with patch.dict(sys.modules, fake_lseg(rows, seen)):
            quotes = feed_api.take_snapshot(object(), ["ZBH.N", "AAPL", "NOPE.N"])
        self.assertEqual(quotes, {"ZBH.N": {"ric": "/ZBH.N", "price": 107.5},
                                  "AAPL": {"ric": "/AAPL.OQ", "price": 280.0}})
        self.assertIn("/NOPE.N", seen)
        self.assertEqual(seen[-1], "closed")  # the stream is always closed

    def test_route_uses_the_open_session_and_refuses_bad_input(self):
        from fastapi import HTTPException
        app = feed_api.make_app({"AAPL": "/AAPL.O"})  # lifespan not run: the streaming worker never starts
        app.state.price_store.session = "streaming-session"
        route = next(r.endpoint for r in app.routes if getattr(r, "path", "") == "/snapshot")
        local = types.SimpleNamespace(client=types.SimpleNamespace(host="127.0.0.1"))
        with patch.object(feed_api, "take_snapshot", return_value={"ZBH.N": {"ric": "ZBH.N", "price": 1.0}}) as snap:
            body = route(local, "ZBH.N,KO")
        self.assertEqual(snap.call_args.args, ("streaming-session", ["KO", "ZBH.N"]))
        self.assertEqual(body["missing"], ["KO"])
        cases = [(local, ""), (local, "A;B"), (local, ",".join(f"S{i}" for i in range(501))),
                 (types.SimpleNamespace(client=types.SimpleNamespace(host="203.0.113.9")), "KO")]
        for request, symbols in cases:
            with self.assertRaises(HTTPException):
                route(request, symbols)
        with patch.object(feed_api, "take_snapshot", side_effect=RuntimeError("down")):
            with self.assertRaises(HTTPException) as error:
                route(local, "KO")
        self.assertEqual(error.exception.status_code, 503)

if __name__ == "__main__":
    unittest.main()
