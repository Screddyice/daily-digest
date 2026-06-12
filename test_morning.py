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

    def test_llm_body_used_verbatim_when_provided(self):
        llm_body = "💪 You\n\n• Activity: trending down.\n\n🐕 Bella\n\n• Activity: steady."
        out = morning.build_digest(TODAY, daily_by_metric=_you_data(),
                                   bella_section="🐕 Bella (fallback)",
                                   llm_body=llm_body)
        self.assertIn("Morning Digest", out)
        self.assertIn("• Activity: trending down.", out)
        self.assertNotIn("(fallback)", out)

    def test_falls_back_to_deterministic_when_llm_body_none(self):
        out = morning.build_digest(TODAY, daily_by_metric=_you_data(),
                                   bella_section="🐕 Bella\n\n• Activity: steady.",
                                   llm_body=None)
        self.assertIn("💪 You", out)
        self.assertIn("🐕 Bella", out)


class AlertWiringTests(unittest.TestCase):
    def test_troublesome_pattern_reaches_the_sender(self):
        import tempfile
        from pathlib import Path
        you = _you_data()
        you["blood_oxygen_saturation"] = {
            d: 92.0 for d in you["blood_oxygen_saturation"]}
        sent = []
        with tempfile.TemporaryDirectory() as tmp:
            morning.run_alerts(you, {"steps": {}, "sleep": {}}, TODAY,
                               send=lambda lines: sent.extend(lines),
                               state_path=Path(tmp) / "s.json")
        self.assertTrue(any("oxygen" in a.lower() for a in sent))

    def test_healthy_data_sends_nothing(self):
        import tempfile
        from pathlib import Path
        sent = []
        with tempfile.TemporaryDirectory() as tmp:
            morning.run_alerts(_you_data(), {"steps": {}, "sleep": {}}, TODAY,
                               send=lambda lines: sent.extend(lines),
                               state_path=Path(tmp) / "s.json")
        self.assertEqual(sent, [])


if __name__ == "__main__":
    unittest.main()
