"""Daily AI spend cap: calls that start together must not all pass a check made on the recorded spend alone."""

import sys
import threading
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

import deepseek_client as ds  # noqa: E402

TINY_CAP_CNY = 0.03  # one 4000-token call reserves about 0.032


class SpendReservationTest(unittest.TestCase):
    def test_second_concurrent_call_is_refused_until_the_first_finishes(self):
        in_flight, release = threading.Event(), threading.Event()

        def slow_send(*_args):
            in_flight.set()
            release.wait(5)
            return {"choices": [{"message": {"content": "ok"}}], "usage": {}}

        with mock.patch.object(ds, "DAILY_CAP_CNY", TINY_CAP_CNY), \
             mock.patch.object(ds, "api_key", return_value="test-key"), \
             mock.patch.object(ds, "today_cost", return_value=0.0), \
             mock.patch.object(ds, "record_usage"), \
             mock.patch.object(ds, "_send_deepseek_request", slow_send):
            first = threading.Thread(target=ds.chat, args=([{"role": "user", "content": "a"}],), kwargs={"feature": "search"})
            first.start()
            self.assertTrue(in_flight.wait(5))
            with self.assertRaises(ds.DailyCapReached):
                ds.chat([{"role": "user", "content": "b"}], feature="search")
            release.set()
            first.join(5)
            content, _ = ds.chat([{"role": "user", "content": "c"}], feature="search")
        self.assertEqual(content, "ok")
        self.assertEqual(ds._reserved_cny.get("capped", 0.0), 0.0)

    def test_uncapped_feature_is_never_refused(self):
        reply = {"choices": [{"message": {"content": "ok"}}], "usage": {}}
        with mock.patch.object(ds, "DAILY_CAP_CNY", 0.0), \
             mock.patch.object(ds, "api_key", return_value="test-key"), \
             mock.patch.object(ds, "_send_deepseek_request", return_value=reply):
            self.assertEqual(ds.chat([{"role": "user", "content": "x"}])[0], "ok")


if __name__ == "__main__":
    unittest.main()
