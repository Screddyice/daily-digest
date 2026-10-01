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


class NoCorpusConfiguredTests(unittest.TestCase):
    """A host with no corpus DB configured (no RDS_URL) must degrade to empty
    (You section drops), never crash the whole digest."""

    def test_load_corpus_config_returns_empty_without_rds_url(self):
        with mock.patch.dict(health.os.environ, {}, clear=True):
            self.assertEqual(health.load_corpus_config(), ("", ""))

    def test_load_corpus_config_reports_configured_with_rds_url(self):
        with mock.patch.dict(health.os.environ, {"RDS_URL": "postgresql://x"}, clear=True):
            base, token = health.load_corpus_config()
            self.assertTrue(base and token)

    def test_fetch_daily_by_metric_short_circuits_without_creds(self):
        called = []

        def fetch_should_not_run(**kwargs):
            called.append(kwargs["metric"])
            return []

        out = health.fetch_daily_by_metric(fetch=fetch_should_not_run,
                                           config=lambda: ("", ""))
        self.assertEqual(out, {})
        self.assertEqual(called, [])  # no corpus reads attempted


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
        self.assertIn("📵 No phone health data in the last 30 days — check the ChatGPT health push.", out)

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


class PhoneOnlyMetricsTests(unittest.TestCase):
    """Shawn's Apple Watch went missing in 2026-09. What the iPhone still
    measures is the whole of the section now."""

    @staticmethod
    def _phone(end: date) -> dict:
        return {
            "step_count": _series(end, 8, 8123),
            "distance_walking_running": _series(end, 8, 6240),
            "flights_climbed": _series(end, 8, 11),
            "walking_speed": _series(end, 8, 1.34),
            "walking_asymmetry": _series(end, 8, 1.2),
            "walking_double_support": _series(end, 8, 28.4),
        }

    def test_distance_reads_in_km_not_metres(self):
        out = health.render_section(self._phone(TODAY), TODAY)
        self.assertIn("6.2 km", out)
        self.assertNotIn("6,240 km", out)

    def test_flights_and_gait_render(self):
        out = health.render_section(self._phone(TODAY), TODAY)
        self.assertIn("11 flights", out)
        self.assertIn("*Gait:*", out)
        self.assertIn("1.34 m/s", out)
        self.assertIn("28.4% double support", out)

    def test_gait_block_is_freshness_guarded(self):
        out = health.render_section(self._phone(TODAY - timedelta(days=8)), TODAY)
        self.assertNotIn("*Gait:*", out)

    def test_absent_wrist_metrics_leave_activity_intact(self):
        """A metric nothing writes contributes no part, rather than a blank."""
        out = health.render_section(self._phone(TODAY), TODAY)
        self.assertIn("*Activity", out)
        self.assertNotIn("kcal", out)
        self.assertNotIn("min exercise", out)
        self.assertIn("recovery & sleep unavailable", out)

    def test_wrist_metrics_return_without_a_code_change(self):
        data = dict(self._phone(TODAY))
        data["active_energy"] = _series(TODAY, 8, 412)
        data["heart_rate_variability"] = _series(TODAY, 8, 38)
        data["resting_heart_rate"] = _series(TODAY, 8, 56)
        out = health.render_section(data, TODAY)
        self.assertIn("412 kcal", out)
        self.assertIn("*HRV:*", out)
        self.assertIn("⌚ Watch on", out)

    def test_fetch_list_covers_every_rendered_metric(self):
        wanted = set()

        def fetch(*, base_url, token, metric, days):
            wanted.add(metric)
            return []

        health.fetch_daily_by_metric(fetch=fetch, config=lambda: ("direct_db", "dsn"))

        rendered = {m for m, *_ in health.ACTIVITY_METRICS} | {m for m, *_ in health.GAIT_METRICS}
        self.assertTrue(rendered <= wanted, rendered - wanted)
