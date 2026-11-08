import threading
import time
import unittest

from portfolio_api import ReportCache


class ReportCacheTests(unittest.TestCase):
    def test_many_viewers_share_one_load(self):
        calls = []

        def loader():
            calls.append(1)
            time.sleep(0.05)
            return {"n": len(calls)}

        cache = ReportCache(loader, ttl_seconds=60)
        threads = [threading.Thread(target=cache.get) for _ in range(30)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(len(calls), 1)

    def test_viewers_get_previous_snapshot_while_refresh_runs(self):
        release = threading.Event()
        values = iter([{"v": 1}, {"v": 2}])

        def loader():
            value = next(values)
            if value["v"] == 2:
                release.wait(2)
            return value

        cache = ReportCache(loader, ttl_seconds=0)
        cache.get()
        refresher = threading.Thread(target=cache.get)
        refresher.start()
        time.sleep(0.05)
        started = time.monotonic()
        self.assertEqual(cache.get(), {"v": 1})  # not blocked behind the slow refresh
        self.assertLess(time.monotonic() - started, 0.5)
        release.set()
        refresher.join()

    def test_failed_refresh_keeps_last_good_snapshot(self):
        results = iter([{"v": 1}, OSError("archive unreadable")])

        def loader():
            value = next(results)
            if isinstance(value, Exception):
                raise value
            return value

        cache = ReportCache(loader, ttl_seconds=0)
        cache.get()
        with self.assertLogs("portfolio_api", "ERROR"):
            self.assertEqual(cache.get(), {"v": 1})

    def test_first_load_failure_is_raised(self):
        cache = ReportCache(lambda: 1 / 0, ttl_seconds=0)
        with self.assertRaises(ZeroDivisionError):
            cache.get()


if __name__ == "__main__":
    unittest.main()
