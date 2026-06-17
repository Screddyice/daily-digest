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


MEETINGS = "📅 Meetings\n\n9:00 AM — Rivus sync\n  • With Andres"


class BuildDigestTests(unittest.TestCase):
    def test_digest_is_you_bella_then_meetings_last(self):
        out = morning.build_digest(TODAY, daily_by_metric=_you_data(),
                                   bella_section="🐕 Bella\n\n• Activity: steady.",
                                   meetings_section=MEETINGS)
        self.assertIn("Morning Digest", out)
        self.assertIn("💪 You", out)
        self.assertIn("🐕 Bella", out)
        self.assertTrue(out.index("📅 Meetings") > out.index("🐕 Bella"))
        self.assertIn("9:00 AM — Rivus sync", out)

    def test_empty_meetings_section_is_omitted(self):
        out = morning.build_digest(TODAY, daily_by_metric=_you_data(),
                                   bella_section="🐕 Bella\n\n• Activity: steady.",
                                   meetings_section="")
        self.assertNotIn("Meetings", out)
        self.assertNotIn("📅", out)

    def test_you_section_stays_numberless_while_bella_may_carry_numbers(self):
        """The LLM-written (or deterministic) You section is numberless; Bella's
        section is allowed real numbers — they must survive into the digest."""
        out = morning.build_digest(
            TODAY, daily_by_metric=_you_data(),
            bella_section="🐕 Bella\n\n• Drinking: 14 events today, drinking about as usual.",
            llm_body=None, meetings_section="")
        lines = out.splitlines()
        you_idx = next(i for i, l in enumerate(lines) if l.startswith("💪"))
        bella_idx = next(i for i, l in enumerate(lines) if l.startswith("🐕"))
        you_body = "\n".join(lines[you_idx + 1:bella_idx])
        self.assertNotRegex(you_body, r"\d")     # You section: no numbers
        self.assertIn("14 events", out)          # Bella's numbers survive

    def test_llm_writes_you_and_bella_section_is_appended_with_numbers(self):
        """The LLM body is only the You narrative; Bella's numeric section is
        always appended deterministically so her real numbers reach the digest."""
        llm_body = "💪 You\n\n• Activity: trending down."
        out = morning.build_digest(
            TODAY, daily_by_metric=_you_data(),
            bella_section="🐕 Bella\n\n• Drinking: 14 events today, drinking about as usual.",
            llm_body=llm_body, meetings_section=MEETINGS)
        self.assertIn("Morning Digest", out)
        self.assertIn("• Activity: trending down.", out)   # You narrative verbatim
        self.assertIn("🐕 Bella", out)                      # Bella appended
        self.assertIn("14 events", out)                     # numbers survive
        self.assertTrue(out.index("🐕 Bella") > out.index("💪 You"))
        self.assertTrue(out.rstrip().endswith("• With Andres"))

    def test_falls_back_to_deterministic_when_llm_body_none(self):
        out = morning.build_digest(TODAY, daily_by_metric=_you_data(),
                                   bella_section="🐕 Bella\n\n• Activity: steady.",
                                   llm_body=None, meetings_section=MEETINGS)
        self.assertIn("💪 You", out)
        self.assertIn("🐕 Bella", out)
        self.assertIn("📅 Meetings", out)


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
