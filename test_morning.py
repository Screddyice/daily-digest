"""Composition tests for the trends-only morning digest. Stdlib only:

    python3 -m unittest test_morning -v
"""
import re
import unittest
from datetime import date, timedelta

import morning

TODAY = date(2026, 6, 12)


def _series(end: date, n: int, val: float) -> dict:
    return {(end - timedelta(days=i)).isoformat(): val for i in range(n)}


def _you_data(end: date = TODAY) -> dict:
    return {
        "step_count": _series(end, 17, 5000),
        "active_energy": _series(end, 17, 300),
        "apple_exercise_time": _series(end, 17, 25),
        "heart_rate_variability": _series(end, 17, 45),
        "resting_heart_rate": _series(end, 17, 60),
        "blood_oxygen_saturation": _series(end, 17, 97),
        "sleep_analysis": _series(end, 17, 7.5),
    }


class BuildDigestTests(unittest.TestCase):
    def test_digest_is_you_plus_bella_only(self):
        out = morning.build_digest(TODAY, daily_by_metric=_you_data(),
                                   bella_section="🐕 Bella\n\n• Activity: steady.")
        self.assertIn("Morning Digest", out)
        self.assertIn("💪 You", out)
        self.assertIn("🐕 Bella", out)
        self.assertNotIn("Meetings", out)
        self.assertNotIn("📅", out)

    def test_no_numbers_outside_the_date_header(self):
        out = morning.build_digest(TODAY, daily_by_metric=_you_data(),
                                   bella_section="🐕 Bella\n\n• Activity: steady.")
        body = "\n".join(l for l in out.splitlines()
                         if "Morning Digest" not in l and not l.startswith("💪"))
        self.assertNotRegex(body, r"\d")


if __name__ == "__main__":
    unittest.main()
