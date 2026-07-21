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


class PhoneStateTests(unittest.TestCase):
    """Freshness guard for iPhone-sourced activity metrics."""

    @staticmethod
    def _activity(end: date) -> dict:
        return {
            "step_count": _series(end, 8, 3138),
            "active_energy": _series(end, 8, 86),
            "apple_exercise_time": _series(end, 8, 2),
        }

    def test_fresh_phone_renders_comparisons_no_stale_line(self):
        out = health.render_section(self._activity(TODAY), TODAY)
        self.assertIn("vs yesterday", out)
        self.assertIn("*Activity", out)
        self.assertNotIn("📵", out)

    def test_stale_phone_shows_gap_line_and_suppresses_comparisons(self):
        out = health.render_section(self._activity(TODAY - timedelta(days=8)), TODAY)
        self.assertIn("📵 No phone health data for 8 days (last data May 19)", out)
        self.assertIn("Last activity (May 19): 3,138 steps · 86 kcal · 2 min exercise", out)
        self.assertNotIn("vs yesterday", out)
        self.assertNotIn("*Activity", out)
        self.assertNotIn("average day", out)

    def test_gap_two_days_is_fresh(self):
        out = health.render_section(self._activity(TODAY - timedelta(days=2)), TODAY)
        self.assertNotIn("📵", out)
        self.assertIn("*Activity", out)

    def test_gap_three_days_is_stale(self):
        out = health.render_section(self._activity(TODAY - timedelta(days=3)), TODAY)
        self.assertIn("📵 No phone health data for 3 days", out)

    def test_no_activity_data_shows_thirty_day_message(self):
        data = {"heart_rate_variability": _series(TODAY - timedelta(days=20), 8, 45)}
        out = health.render_section(data, TODAY)
        self.assertIn("📵 No phone health data in the last 30 days — check Health Auto Export.", out)

    def test_stale_phone_and_stale_wrist_show_both_lines(self):
        data = self._activity(TODAY - timedelta(days=8))
        data["heart_rate_variability"] = _series(TODAY - timedelta(days=34), 8, 38)
        out = health.render_section(data, TODAY)
        self.assertIn("📵 No phone health data for 8 days", out)
        self.assertIn("⌚ Watch off 34 days", out)

    def test_stale_last_known_line_skips_older_metrics(self):
        data = {
            "step_count": _series(TODAY - timedelta(days=8), 8, 3138),
            "active_energy": _series(TODAY - timedelta(days=20), 8, 86),
        }
        out = health.render_section(data, TODAY)
        self.assertIn("Last activity (May 19): 3,138 steps", out)
        self.assertNotIn("kcal", out)


if __name__ == "__main__":
    unittest.main()
