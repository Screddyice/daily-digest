"""Unit tests for the Fi collar (Bella) fetch + history layer. Stdlib only:

    python3 -m unittest test_bella -v

Network is injected; these tests cover response parsing, the local step-history
accumulation, and graceful degradation when Fi creds are absent.
"""
import json
import tempfile
import unittest
from datetime import date
from pathlib import Path

import bella

TODAY = date(2026, 6, 12)

PETS_RESPONSE = {
    "data": {"currentUser": {"userHouseholds": [{"household": {"pets": [
        {"id": "pet-123", "name": "Bella"},
        {"id": "pet-456", "name": "Rex"},
    ]}}]}}
}

STEPS_RESPONSE = {
    "data": {"pet": {"dailyStepStat": {"totalSteps": 8421, "stepGoal": 12000}}}
}

REST_RESPONSE = {
    "data": {"pet": {"restSummaryFeed": {"restSummaries": [
        {"start": "2026-06-12T07:00:00Z",
         "data": {"sleepAmounts": [{"type": "SLEEP", "duration": 28800},
                                   {"type": "NAP", "duration": 3600}]}},
        {"start": "2026-06-11T07:00:00Z",
         "data": {"sleepAmounts": [{"type": "SLEEP", "duration": 30000}]}},
    ]}}}
}


class ParseTests(unittest.TestCase):
    def test_find_pet_by_name_case_insensitive(self):
        self.assertEqual(bella.find_pet_id(PETS_RESPONSE, "bella"), "pet-123")

    def test_find_pet_missing_returns_none(self):
        self.assertIsNone(bella.find_pet_id(PETS_RESPONSE, "Fido"))

    def test_parse_daily_steps(self):
        self.assertEqual(bella.parse_daily_steps(STEPS_RESPONSE), 8421.0)

    def test_parse_daily_steps_missing_returns_none(self):
        self.assertIsNone(bella.parse_daily_steps({"data": {"pet": {}}}))

    def test_parse_rest_summaries_sums_sleep_and_nap_minutes_per_day(self):
        out = bella.parse_rest_summaries(REST_RESPONSE)
        self.assertEqual(out["2026-06-12"], (28800 + 3600) / 60)
        self.assertEqual(out["2026-06-11"], 30000 / 60)


class HistoryTests(unittest.TestCase):
    def test_history_appends_and_overwrites_per_day(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bella_history.json"
            bella.update_history(path, "steps", "2026-06-11", 7000.0)
            bella.update_history(path, "steps", "2026-06-12", 8421.0)
            bella.update_history(path, "steps", "2026-06-12", 8500.0)  # same-day rerun
            hist = bella.load_history(path)
            self.assertEqual(hist["steps"], {"2026-06-11": 7000.0, "2026-06-12": 8500.0})

    def test_load_history_missing_file_is_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(bella.load_history(Path(tmp) / "nope.json"), {})


class BuildSectionTests(unittest.TestCase):
    def test_no_credentials_degrades_gracefully(self):
        out = bella.build_section(TODAY, env={}, history_path=Path("/nonexistent"))
        self.assertIn("Bella", out)
        self.assertIn("not configured", out.lower())

    def test_build_section_with_injected_gql(self):
        responses = {"pets": PETS_RESPONSE, "steps": STEPS_RESPONSE, "rest": REST_RESPONSE}

        def fake_gql(kind, **kw):
            return responses[kind]

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "hist.json"
            # seed enough history that a trend is readable
            for i in range(17):
                d = date(2026, 5, 26 + i) if 26 + i <= 31 else date(2026, 6, 26 + i - 31)
                bella.update_history(path, "steps", d.isoformat(), 8000.0)
            out = bella.build_section(TODAY, env={"FI_EMAIL": "x", "FI_PASSWORD": "y"},
                                      history_path=path, gql=fake_gql, pet_name="Bella")
            self.assertIn("🐕 Bella", out)
            self.assertIn("Activity:", out)
            self.assertIn("Sleep:", out)
            # today's live reading landed in the history file
            self.assertEqual(bella.load_history(path)["steps"][TODAY.isoformat()], 8421.0)

    def test_fi_error_degrades_not_raises(self):
        def boom(kind, **kw):
            raise OSError("fi down")

        out = bella.build_section(TODAY, env={"FI_EMAIL": "x", "FI_PASSWORD": "y"},
                                  history_path=Path("/nonexistent/h.json"), gql=boom)
        self.assertIn("Bella", out)
        self.assertIn("unavailable", out.lower())


if __name__ == "__main__":
    unittest.main()
