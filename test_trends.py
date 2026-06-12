"""Unit tests for the trends-only digest sections. Stdlib only:

    python3 -m unittest test_trends -v

The spec: the digest tells Shawn only DIRECTIONS — increasing / decreasing /
steady — never numbers. Covers activity & exercise, lung health (SpO₂),
stress signals (HRV + resting HR), sleep sufficiency, and getting-sick hints.
Same treatment for Bella the dog (Fi collar series).
"""
import re
import unittest
from datetime import date, timedelta

import trends

TODAY = date(2026, 6, 12)


def _series(end: date, n: int, vals) -> dict:
    """n consecutive daily values ending (inclusive) at `end`.

    `vals` is a constant or a list ordered oldest→newest of length n.
    """
    if not isinstance(vals, (list, tuple)):
        vals = [vals] * n
    days = [(end - timedelta(days=n - 1 - i)).isoformat() for i in range(n)]
    return dict(zip(days, vals))


def _flat_then(end: date, base: float, recent: list) -> dict:
    """14 days of `base`, then the values in `recent` (oldest→newest) ending at `end`."""
    n = 14 + len(recent)
    return _series(end, n, [base] * 14 + list(recent))


class ClassifyTrendTests(unittest.TestCase):
    def test_rising_series_classifies_up(self):
        daily = _flat_then(TODAY, 5000, [6500, 7000, 7500])
        self.assertEqual(trends.classify_trend(daily, TODAY), "up")

    def test_falling_series_classifies_down(self):
        daily = _flat_then(TODAY, 5000, [4000, 3800, 3500])
        self.assertEqual(trends.classify_trend(daily, TODAY), "down")

    def test_flat_series_classifies_steady(self):
        daily = _series(TODAY, 17, 5000)
        self.assertEqual(trends.classify_trend(daily, TODAY), "steady")

    def test_small_wiggle_is_still_steady(self):
        daily = _flat_then(TODAY, 5000, [5100, 4950, 5050])
        self.assertEqual(trends.classify_trend(daily, TODAY), "steady")

    def test_too_little_data_returns_none(self):
        daily = _series(TODAY, 3, 5000)
        self.assertIsNone(trends.classify_trend(daily, TODAY))

    def test_empty_returns_none(self):
        self.assertIsNone(trends.classify_trend({}, TODAY))

    def test_strong_move_classifies_sharply(self):
        daily = _flat_then(TODAY, 50, [40, 38, 35])  # ~25% drop
        self.assertEqual(trends.classify_trend(daily, TODAY), "down")
        self.assertTrue(trends.is_sharp_move(daily, TODAY))

    def test_mild_move_is_not_sharp(self):
        daily = _flat_then(TODAY, 50, [46, 46, 45])
        self.assertFalse(trends.is_sharp_move(daily, TODAY))


class StalenessTests(unittest.TestCase):
    def test_fresh_data_not_stale(self):
        daily = _series(TODAY, 17, 5000)
        self.assertEqual(trends.staleness_days({"step_count": daily}, TODAY), 0)

    def test_frozen_data_reports_age_of_newest_metric(self):
        old = _series(TODAY - timedelta(days=3), 17, 5000)
        self.assertEqual(trends.staleness_days({"step_count": old}, TODAY), 3)

    def test_no_data_reports_none(self):
        self.assertIsNone(trends.staleness_days({}, TODAY))
        self.assertIsNone(trends.staleness_days({"step_count": {}}, TODAY))


class YouSectionTests(unittest.TestCase):
    def _data(self, **over):
        base = {
            "step_count": _series(TODAY, 17, 5000),
            "active_energy": _series(TODAY, 17, 300),
            "apple_exercise_time": _series(TODAY, 17, 25),
            "heart_rate_variability": _series(TODAY, 17, 45),
            "resting_heart_rate": _series(TODAY, 17, 60),
            "blood_oxygen_saturation": _series(TODAY, 17, 97),
            "sleep_analysis": _series(TODAY, 17, 7.5),
        }
        base.update(over)
        return base

    def test_no_numbers_anywhere_in_body(self):
        out = trends.render_you_section(self._data(), TODAY)
        body = "\n".join(l for l in out.splitlines() if not l.startswith("💪"))
        self.assertNotRegex(body, r"\d")

    def test_all_steady_reads_steady(self):
        out = trends.render_you_section(self._data(), TODAY)
        self.assertIn("Activity & exercise:", out)
        self.assertIn("steady", out.lower())

    def test_rising_activity_reads_up(self):
        out = trends.render_you_section(self._data(
            step_count=_flat_then(TODAY, 4000, [6000, 6500, 7000]),
            apple_exercise_time=_flat_then(TODAY, 20, [35, 40, 45]),
            active_energy=_flat_then(TODAY, 250, [380, 400, 420]),
        ), TODAY)
        act = next(l for l in out.splitlines() if "Activity & exercise" in l)
        self.assertIn("up", act.lower())

    def test_lung_health_line_present_and_calm_when_normal(self):
        out = trends.render_you_section(self._data(), TODAY)
        lung = next(l for l in out.splitlines() if "Lungs" in l)
        self.assertIn("normal", lung.lower())

    def test_low_spo2_flagged_without_numbers(self):
        out = trends.render_you_section(self._data(
            blood_oxygen_saturation=_flat_then(TODAY, 97, [93, 92, 92]),
        ), TODAY)
        lung = next(l for l in out.splitlines() if "Lungs" in l)
        self.assertIn("below", lung.lower())
        self.assertNotRegex(lung, r"\d")

    def test_stress_flagged_when_hrv_down_and_rhr_up(self):
        out = trends.render_you_section(self._data(
            heart_rate_variability=_flat_then(TODAY, 45, [38, 36, 34]),
            resting_heart_rate=_flat_then(TODAY, 60, [66, 68, 70]),
        ), TODAY)
        stress = next(l for l in out.splitlines() if "Stress" in l)
        self.assertTrue("stress" in stress.lower() or "strain" in stress.lower())
        self.assertNotIn("no signs", stress.lower())

    def test_stress_calm_when_recovery_steady(self):
        out = trends.render_you_section(self._data(), TODAY)
        stress = next(l for l in out.splitlines() if "Stress" in l)
        self.assertIn("no", stress.lower())

    def test_sleep_debt_flagged_when_short_nights(self):
        out = trends.render_you_section(self._data(
            sleep_analysis=_flat_then(TODAY, 7.5, [5.5, 5.0, 5.2]),
        ), TODAY)
        sleep = next(l for l in out.splitlines() if "Sleep" in l)
        self.assertIn("short", sleep.lower())

    def test_sleep_absent_says_no_signal_not_fake_ok(self):
        out = trends.render_you_section(self._data(sleep_analysis={}), TODAY)
        sleep = next(l for l in out.splitlines() if "Sleep" in l)
        self.assertTrue("watch" in sleep.lower() or "no sleep data" in sleep.lower())

    def test_illness_watch_fires_on_combined_sharp_moves(self):
        out = trends.render_you_section(self._data(
            heart_rate_variability=_flat_then(TODAY, 45, [34, 31, 28]),
            resting_heart_rate=_flat_then(TODAY, 60, [70, 73, 76]),
            blood_oxygen_saturation=_flat_then(TODAY, 97, [94, 93, 93]),
        ), TODAY)
        ill = next(l for l in out.splitlines() if "Illness" in l)
        self.assertTrue("fighting" in ill.lower() or "coming down" in ill.lower()
                        or "watch" in ill.lower())
        self.assertNotIn("no signals", ill.lower())

    def test_illness_quiet_when_healthy(self):
        out = trends.render_you_section(self._data(), TODAY)
        ill = next(l for l in out.splitlines() if "Illness" in l)
        self.assertIn("no signals", ill.lower())

    def test_stale_data_leads_with_warning(self):
        shift = 3
        old_end = TODAY - timedelta(days=shift)
        data = {k: _series(old_end, 17, v) for k, v in (
            ("step_count", 5000), ("active_energy", 300), ("apple_exercise_time", 25),
            ("heart_rate_variability", 45), ("resting_heart_rate", 60),
            ("blood_oxygen_saturation", 97), ("sleep_analysis", 7.5),
        )}
        out = trends.render_you_section(data, TODAY)
        self.assertIn("⚠️", out)
        self.assertIn("hasn't synced", out)

    def test_fresh_data_has_no_warning(self):
        out = trends.render_you_section(self._data(), TODAY)
        self.assertNotIn("⚠️", out)

    def test_no_data_at_all_degrades_explicitly(self):
        out = trends.render_you_section({}, TODAY)
        self.assertIn("no health data", out.lower())


class BellaSectionTests(unittest.TestCase):
    def test_bella_trends_render_without_numbers(self):
        steps = _flat_then(TODAY, 8000, [10000, 11000, 12000])
        sleep = _series(TODAY, 17, 720.0)
        out = trends.render_pet_section("Bella", {"steps": steps, "sleep": sleep}, TODAY)
        self.assertIn("Bella", out)
        self.assertIn("Activity:", out)
        self.assertIn("Sleep:", out)
        body = "\n".join(l for l in out.splitlines() if "Bella" not in l)
        self.assertNotRegex(body, r"\d")
        act = next(l for l in out.splitlines() if "Activity" in l)
        self.assertIn("up", act.lower())

    def test_bella_no_data_degrades_explicitly(self):
        out = trends.render_pet_section("Bella", {}, TODAY)
        self.assertIn("Bella", out)
        self.assertIn("no", out.lower())

    def test_bella_stale_data_warns(self):
        old_end = TODAY - timedelta(days=4)
        out = trends.render_pet_section(
            "Bella", {"steps": _series(old_end, 17, 8000)}, TODAY)
        self.assertIn("⚠️", out)


if __name__ == "__main__":
    unittest.main()
