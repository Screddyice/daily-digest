"""Unit tests for the watch-aware live health section. Stdlib only:

    python3 -m unittest test_health -v
"""
import unittest
from datetime import date, timedelta
from unittest import mock

import health

TODAY = date(2026, 5, 27)


def _series(end: date, n: int, val: float) -> dict:
    """n consecutive daily values ending (inclusive) at `end`."""
    return {(end - timedelta(days=i)).isoformat(): val for i in range(n)}


class WatchStateTests(unittest.TestCase):
    def test_watch_on_shows_recovery_and_sleep_no_gap_line(self):
        data = {
            "step_count": _series(TODAY, 8, 10000),
            "active_energy": _series(TODAY, 8, 200),
            "apple_exercise_time": _series(TODAY, 8, 20),
            "heart_rate_variability": _series(TODAY, 8, 45),
            "resting_heart_rate": _series(TODAY, 8, 60),
            "blood_oxygen_saturation": _series(TODAY, 8, 97),
            "sleep_analysis": _series(TODAY, 8, 7.5),
        }
        out = health.render_section(data, TODAY)
        self.assertIn("⌚ Watch on", out)
        self.assertIn("HRV", out)
        self.assertNotIn("Watch off", out)
        self.assertIn("10,000 steps", out)
        self.assertIn("7.5 h", out)               # appears as "*Sleep:* 7.5 h last night …"
        self.assertIn("vs yesterday", out)         # explicit comparison label
        self.assertIn("vs week ago", out)

    def test_watch_off_keeps_activity_and_states_the_gap(self):
        last_watch = TODAY - timedelta(days=6)
        data = {
            "step_count": _series(TODAY, 10, 12000),           # iPhone, still fresh
            "active_energy": _series(TODAY, 10, 190),
            "heart_rate_variability": _series(last_watch, 8, 43),
            "resting_heart_rate": _series(last_watch, 8, 67),
        }
        out = health.render_section(data, TODAY)
        self.assertIn("⌚ Watch off 6 days", out)
        self.assertNotIn("Recovery:", out)
        self.assertIn("12,000 steps", out)   # activity must NOT go dark
        self.assertIn("Last HRV", out)

    def test_no_watch_data_degrades_cleanly(self):
        out = health.render_section({"step_count": _series(TODAY, 5, 8000)}, TODAY)
        self.assertIn("No recent Apple Watch data", out)
        self.assertIn("8,000 steps", out)
        self.assertNotIn("Recovery:", out)

    def test_build_section_survives_a_metric_fetch_error(self):
        def boom(*, base_url, token, metric, days):
            if metric == "heart_rate_variability":
                raise RuntimeError("HAE 500")
            return [{"date": f"{TODAY.isoformat()}T12:00:00.000Z", "qty": 9000.0}]

        out = health.build_section(today=TODAY, fetch=boom, config=lambda: ("http://x", "tok"))
        self.assertIn("Health", out)
        self.assertIn("steps", out)


class NoHAEConfiguredTests(unittest.TestCase):
    """A cloud sandbox with no HAE env vars and no connector file must degrade to
    empty (You section drops), never crash the whole digest."""

    def test_load_hae_config_returns_empty_when_no_env_and_no_file(self):
        env = {"HAE_CONNECTOR_JSON": "/nonexistent/definitely-not-here.json"}
        with mock.patch.dict(health.os.environ, env, clear=True):
            self.assertEqual(health.load_hae_config(), ("", ""))

    def test_fetch_daily_by_metric_short_circuits_without_creds(self):
        called = []

        def fetch_should_not_run(**kwargs):
            called.append(kwargs["metric"])
            return []

        out = health.fetch_daily_by_metric(fetch=fetch_should_not_run,
                                           config=lambda: ("", ""))
        self.assertEqual(out, {})
        self.assertEqual(called, [])  # no HAE requests attempted


if __name__ == "__main__":
    unittest.main()
