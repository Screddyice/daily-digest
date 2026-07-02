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


class TwoPointDirectionTests(unittest.TestCase):
    """With only two readings (no 7-day baseline), compare latest vs previous."""

    def test_two_readings_up(self):
        daily = {"2026-06-11": 4000, "2026-06-12": 6000}
        self.assertEqual(trends.direction(daily, TODAY), "up")

    def test_two_readings_down(self):
        daily = {"2026-06-11": 6000, "2026-06-12": 4000}
        self.assertEqual(trends.direction(daily, TODAY), "down")

    def test_two_readings_steady(self):
        daily = {"2026-06-11": 5000, "2026-06-12": 5050}
        self.assertEqual(trends.direction(daily, TODAY), "steady")

    def test_zero_to_something_is_up(self):
        daily = {"2026-06-11": 0, "2026-06-12": 5}   # behavior appeared today
        self.assertEqual(trends.direction(daily, TODAY), "up")

    def test_zero_to_zero_is_steady(self):
        daily = {"2026-06-11": 0, "2026-06-12": 0}
        self.assertEqual(trends.direction(daily, TODAY), "steady")

    def test_one_reading_is_none(self):
        self.assertIsNone(trends.direction({"2026-06-12": 5000}, TODAY))

    def test_empty_is_none(self):
        self.assertIsNone(trends.direction({}, TODAY))

    def test_direction_prefers_statistical_when_baseline_exists(self):
        daily = _flat_then(TODAY, 5000, [6500, 7000, 7500])  # 17 days, clear rise
        self.assertEqual(trends.direction(daily, TODAY), "up")


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

    def _data_with_activity_up(self, **over):
        """The steady baseline plus ONE real deviation (rising activity) so the
        section renders — lets us check that OTHER calm metrics are DROPPED."""
        return self._data(
            step_count=_flat_then(TODAY, 4000, [6000, 6500, 7000]),
            active_energy=_flat_then(TODAY, 250, [380, 400, 420]),
            apple_exercise_time=_flat_then(TODAY, 20, [35, 40, 45]),
            **over,
        )

    def test_no_numbers_anywhere_in_body(self):
        out = trends.render_you_section(self._data_with_activity_up(), TODAY)
        body = "\n".join(l for l in out.splitlines() if not l.startswith("💪"))
        self.assertNotRegex(body, r"\d")

    def test_all_steady_drops_the_section(self):
        # Every metric at its usual — nothing worth surfacing, so no section.
        self.assertIsNone(trends.render_you_section(self._data(), TODAY))

    def test_rising_activity_reads_up(self):
        out = trends.render_you_section(self._data(
            step_count=_flat_then(TODAY, 4000, [6000, 6500, 7000]),
            apple_exercise_time=_flat_then(TODAY, 20, [35, 40, 45]),
            active_energy=_flat_then(TODAY, 250, [380, 400, 420]),
        ), TODAY)
        act = next(l for l in out.splitlines() if "Activity & exercise" in l)
        self.assertIn("up", act.lower())

    def test_lungs_line_absent_when_normal(self):
        # Calm, in-range blood oxygen is dropped, not rendered as "steady, normal".
        out = trends.render_you_section(self._data_with_activity_up(), TODAY)
        self.assertNotIn("Lungs", out)

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

    def test_stress_line_absent_when_calm(self):
        out = trends.render_you_section(self._data_with_activity_up(), TODAY)
        self.assertNotIn("Stress", out)

    def test_sleep_debt_flagged_when_short_nights(self):
        out = trends.render_you_section(self._data(
            sleep_analysis=_flat_then(TODAY, 7.5, [5.5, 5.0, 5.2]),
        ), TODAY)
        sleep = next(l for l in out.splitlines() if "Sleep" in l)
        self.assertIn("short", sleep.lower())

    def test_sleep_line_absent_when_on_track_or_missing(self):
        # Sleep at your usual OR no sleep signal at all — dropped, not announced.
        out = trends.render_you_section(self._data_with_activity_up(sleep_analysis={}), TODAY)
        self.assertNotIn("Sleep", out)

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

    def test_illness_line_absent_when_healthy(self):
        out = trends.render_you_section(self._data_with_activity_up(), TODAY)
        self.assertNotIn("Illness", out)

    def test_stale_data_leads_with_warning(self):
        # A source that's stale but DOES deviate still carries the ⚠️ when reached.
        old_end = TODAY - timedelta(days=3)
        data = {
            "step_count": _flat_then(old_end, 4000, [6000, 6500, 7000]),
            "active_energy": _flat_then(old_end, 250, [380, 400, 420]),
            "apple_exercise_time": _flat_then(old_end, 20, [35, 40, 45]),
            "heart_rate_variability": _series(old_end, 17, 45),
            "resting_heart_rate": _series(old_end, 17, 60),
            "blood_oxygen_saturation": _series(old_end, 17, 97),
            "sleep_analysis": _series(old_end, 17, 7.5),
        }
        out = trends.render_you_section(data, TODAY)
        self.assertIsNotNone(out)
        self.assertIn("⚠️", out)
        self.assertIn("hasn't synced", out)

    def test_fresh_data_has_no_warning(self):
        out = trends.render_you_section(self._data_with_activity_up(), TODAY)
        self.assertNotIn("⚠️", out)

    def test_no_data_at_all_drops_the_section(self):
        # No health data — the section is dropped entirely, the gap is not announced.
        self.assertIsNone(trends.render_you_section({}, TODAY))


class BellaSectionTests(unittest.TestCase):
    def test_bella_trends_render_without_numbers(self):
        steps = _flat_then(TODAY, 8000, [10000, 11000, 12000])   # up
        sleep = _flat_then(TODAY, 720.0, [560, 540, 520])         # down
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

    def test_bella_behaviors_render_with_event_counts_and_direction(self):
        """Moving behaviors show Bella's real counts + direction; steady ones are
        DROPPED (no 'about as usual'). Steps stays numberless."""
        series = {
            "steps": _flat_then(TODAY, 8000, [10000, 11000, 12000]),  # up
            "eating_events": _flat_then(TODAY, 3, [5, 6, 7]),          # rising
            "drinking_events": _series(TODAY, 17, 5),                  # steady -> dropped
            "scratching_events": _flat_then(TODAY, 2, [6, 7, 8]),      # rising (skin?)
            "licking_events": _flat_then(TODAY, 4, [2, 1, 1]),         # falling
            "barking_events": _series(TODAY, 17, 1),                   # steady -> dropped
        }
        out = trends.render_pet_section("Bella", series, TODAY)
        eat = next(l for l in out.splitlines() if "Eating" in l)
        self.assertIn("7 event", eat)          # latest reading as a real count
        self.assertIn("more", eat.lower())     # direction wording kept alongside
        lick = next(l for l in out.splitlines() if "Licking" in l)
        self.assertIn("less", lick.lower())
        # steady behaviors are dropped entirely, never rendered as "about as usual"
        self.assertNotIn("Drinking", out)
        self.assertNotIn("Barking", out)
        # steps was NOT selected for numbers — it stays direction-only
        act = next(l for l in out.splitlines() if "Activity" in l)
        self.assertNotRegex(act, r"\d")

    def test_bella_behavior_absent_is_skipped_not_faked(self):
        series = {"steps": _series(TODAY, 17, 8000), "sleep": _series(TODAY, 17, 700.0)}
        out = trends.render_pet_section("Bella", series, TODAY)
        self.assertNotIn("Eating", out)
        self.assertNotIn("Barking", out)

    def test_bella_single_reading_behaviors_are_suppressed(self):
        """Day one of a behavior: one reading can't give a direction, so the line
        is suppressed entirely — no "baseline building" filler. A metric with
        real history (steps) still renders."""
        series = {
            "steps": _flat_then(TODAY, 8000, [10000, 11000, 12000]),  # up (real history)
            "eating_events": {TODAY.isoformat(): 3},
            "licking_events": {TODAY.isoformat(): 5},
        }
        out = trends.render_pet_section("Bella", series, TODAY)
        self.assertNotIn("Eating", out)        # single-reading behaviors omitted
        self.assertNotIn("Licking", out)
        self.assertNotIn("baseline", out.lower())
        # the metric with real history still shows, and stays numberless
        act = next(l for l in out.splitlines() if "Activity" in l)
        self.assertNotRegex(act, r"\d")

    def test_bella_two_readings_already_render_a_direction(self):
        """Only yesterday + today — should still call a direction, not 'not enough'."""
        series = {
            "steps": {"2026-06-11": 6000, "2026-06-12": 9000},
            "eating_events": {"2026-06-11": 2, "2026-06-12": 5},
        }
        out = trends.render_pet_section("Bella", series, TODAY)
        act = next(l for l in out.splitlines() if "Activity" in l)
        self.assertIn("up", act.lower())
        self.assertNotIn("not enough", act.lower())
        eat = next(l for l in out.splitlines() if "Eating" in l)
        self.assertIn("more", eat.lower())

    def test_bella_stale_data_warns(self):
        old_end = TODAY - timedelta(days=4)
        out = trends.render_pet_section(
            "Bella", {"steps": _series(old_end, 17, 8000)}, TODAY)
        self.assertIn("⚠️", out)


class ReadableSignalTests(unittest.TestCase):
    """has_readable_signal — True only when some metric has enough history for a
    real trend; drives "drop the pet section entirely when it's all filler"."""

    def test_true_when_a_metric_has_two_plus_readings(self):
        # two days of sleep is enough for a previous-vs-latest direction
        series = {"sleep": {"2026-06-11": 700.0, "2026-06-12": 500.0}}
        self.assertTrue(trends.has_readable_signal(series, TODAY))

    def test_false_when_every_metric_has_one_reading(self):
        series = {
            "steps": {"2026-06-12": 8421.0},
            "sleep": {"2026-06-12": 480.0},
            "eating_events": {"2026-06-12": 3.0},
        }
        self.assertFalse(trends.has_readable_signal(series, TODAY))

    def test_false_when_series_empty(self):
        self.assertFalse(trends.has_readable_signal({"steps": {}, "sleep": {}}, TODAY))


if __name__ == "__main__":
    unittest.main()
