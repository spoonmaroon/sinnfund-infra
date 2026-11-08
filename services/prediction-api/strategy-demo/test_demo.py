"""Offline checks: no credentials or network required."""

import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import demo
from strategy import target_price


class DemoTest(unittest.TestCase):
    def test_strategy(self):
        self.assertEqual(target_price([98, 100, 102, 104, 96]), 100)
        for values in ([1], [1, 2, 3, 4, float("nan")], [1, 2, 3, 4, -1]):
            with self.assertRaises(ValueError):
                target_price(values)

    def test_lifecycle_and_preview(self):
        with tempfile.TemporaryDirectory() as folder, contextlib.redirect_stdout(io.StringIO()):
            path = Path(folder) / "signal.json"
            file_args = ["--file", str(path)]
            demo.main(["prepare", *file_args])
            original = path.read_text()
            signal = json.loads(original)
            with self.assertRaises(SystemExit):
                demo.main(["prepare", *file_args])
            with patch.object(demo.client, "main", return_value=0) as send:
                demo.main(["create", *file_args])
                demo.main(["edit", "--price", "103", *file_args])
                demo.main(["delete", *file_args])
                send.assert_not_called()
                demo.main(["create", "--send", *file_args])
                first = send.call_args
                demo.main(["create", "--send", *file_args])
                self.assertEqual(send.call_args, first)
                self.assertEqual(first.args[0], ["create", signal["signal_id"], "LIVE",
                                                "DEMO", "100", str(signal["timestamp"])])
                demo.main(["get", *file_args])
                send.assert_called_with(["get", signal["signal_id"]])
                demo.main(["edit", "--price", "103", "--send", *file_args])
                send.assert_called_with(["edit", signal["signal_id"], "--price", "103.0"])
                demo.main(["delete", "--send", *file_args])
                send.assert_called_with(["delete", signal["signal_id"]])
            self.assertEqual(path.read_text(), original)


if __name__ == "__main__":
    unittest.main()
