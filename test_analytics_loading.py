"""A slow archive check must not block the app or create duplicate workers."""
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from data import StudioData
from server import StudioHTTPServer
from test_data import make_fixture
from test_server import FixtureJobs, FixtureHistory


class AnalyticsLoadingTests(unittest.TestCase):
    def test_slow_check_is_single_and_failure_retains_labelled_snapshot(self):
        with tempfile.TemporaryDirectory() as temp:
            root = make_fixture(Path(temp) / 'CV')
            data = StudioData(root)
            entered, release = threading.Event(), threading.Event()

            def slow_check(roles, runs):
                entered.set()
                release.wait(5)
                return {'unique_jobs_read': 7, 'manual_shortlists': []}

            with patch.object(data, '_dashboard_analytics', side_effect=slow_check) as compute:
                server = StudioHTTPServer(('127.0.0.1', 0), root, data=data,
                                          jobs=FixtureJobs(), history=FixtureHistory())
                try:
                    started = time.monotonic()
                    first = server.overview_snapshot()
                    self.assertLess(time.monotonic() - started, 2)
                    self.assertTrue(first['dashboard_analytics']['loading'])
                    self.assertNotIn('unique_jobs_read', first['dashboard_analytics'])
                    self.assertTrue(entered.wait(1))
                    server.overview_snapshot()
                    self.assertEqual(compute.call_count, 1)
                    release.set()
                    for _ in range(100):
                        if not server.analytics_running:
                            break
                        time.sleep(.01)
                    ready = server.overview_snapshot()
                    self.assertEqual(ready['dashboard_analytics']['unique_jobs_read'], 7)
                    self.assertFalse(ready['dashboard_analytics_refreshing'])
                    server.analytics_checked_at = None
                    compute.side_effect = RuntimeError('Fixture read failure')
                    server.overview_snapshot()
                    for _ in range(100):
                        if not server.analytics_running:
                            break
                        time.sleep(.01)
                    failed = server.overview_snapshot()
                    self.assertTrue(failed['dashboard_analytics_error'])
                    self.assertEqual(failed['dashboard_analytics']['unique_jobs_read'], 7)
                finally:
                    release.set()
                    server.server_close()


if __name__ == '__main__':
    unittest.main()
