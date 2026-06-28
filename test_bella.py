"""Unit tests for the Fi collar (Bella) fetch + history layer. Stdlib only:

    python3 -m unittest test_bella -v

Network is injected; these tests cover response parsing, the local step-history
accumulation, and graceful degradation when Fi creds are absent.
"""
import json
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest import mock

import bella

TODAY = date(2026, 6, 12)


class _FakeResp:
    """Minimal urlopen stand-in: usable as a context manager and .read()/.close()."""
    def __init__(self, text: str):
        self._text = text

    def read(self):
        return self._text.encode()

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def close(self):
        pass

PETS_RESPONSE = {
    "data": {"currentUser": {"userHouseholds": [{"household": {"pets": [
        {"id": "pet-123", "name": "Bella"},
        {"id": "pet-456", "name": "Rex"},
    ]}}]}}
}

PROFILE_RESPONSE = {
    "data": {"pet": {
        "name": "Bella", "gender": "FEMALE", "weight": 29.48348,
        "yearOfBirth": 2022, "monthOfBirth": 5, "dayOfBirth": 10,
        "breed": {"name": "Labrador Retriever"},
    }}
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

# Real shape captured from the Fi app (HealthTrends / getPetHealthTrendsForPet).
TRENDS_RESPONSE = {
    "data": {"getPetHealthTrendsForPet": {
        "period": "DAY",
        "genericTrends": [
            {"title": "Rest", "disabled": False, "summaryComponents": {
                "eventsSummary": "0 interruptions", "durationSummary": "6hr 59min",
                "eventsChange": None, "durationChange": None}},
            {"title": "Activity", "disabled": False, "summaryComponents": {
                "eventsSummary": "325 steps", "durationSummary": "0min",
                "eventsChange": None, "durationChange": None}},
        ],
        "behaviorTrends": [
            {"title": "Barking", "disabled": False, "summaryComponents": {
                "eventsSummary": None, "durationSummary": None,
                "eventsChange": None, "durationChange": None}},
            {"title": "Eating", "disabled": False, "summaryComponents": {
                "eventsSummary": "3 events", "durationSummary": "4min",
                "eventsChange": None, "durationChange": None}},
            {"title": "Drinking", "disabled": False, "summaryComponents": {
                "eventsSummary": "5 events", "durationSummary": "2min",
                "eventsChange": None, "durationChange": None}},
            {"title": "Licking", "disabled": False, "summaryComponents": {
                "eventsSummary": "2 events", "durationSummary": "1min",
                "eventsChange": None, "durationChange": None}},
            {"title": "Scratching", "disabled": False, "summaryComponents": {
                "eventsSummary": None, "durationSummary": None,
                "eventsChange": None, "durationChange": None}},
        ],
    }}
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

    def test_parse_rest_summaries_skips_zero_total_days(self):
        """The in-progress day reports 0s before any rest is logged — a real
        zero-rest day is implausible for a dog, so zero means not-yet-recorded
        and must not drag the trend down."""
        resp = {"data": {"pet": {"restSummaryFeed": {"restSummaries": [
            {"start": "2026-06-12T07:00:00Z",
             "data": {"sleepAmounts": [{"type": "SLEEP", "duration": 0},
                                       {"type": "NAP", "duration": 0}]}},
            {"start": "2026-06-11T07:00:00Z",
             "data": {"sleepAmounts": [{"type": "SLEEP", "duration": 30000}]}},
        ]}}}}
        out = bella.parse_rest_summaries(resp)
        self.assertNotIn("2026-06-12", out)
        self.assertIn("2026-06-11", out)


class TrendParseTests(unittest.TestCase):
    def test_parse_count_handles_all_captured_formats(self):
        self.assertEqual(bella.parse_count("2 events"), 2.0)
        self.assertEqual(bella.parse_count("325 steps"), 325.0)
        self.assertEqual(bella.parse_count("0 interruptions"), 0.0)
        self.assertEqual(bella.parse_count("1.6 events/day"), 1.6)
        self.assertEqual(bella.parse_count("2,987 steps/day"), 2987.0)
        self.assertIsNone(bella.parse_count(None))

    def test_parse_minutes_handles_all_captured_formats(self):
        self.assertEqual(bella.parse_minutes("6hr 59min"), 419.0)
        self.assertEqual(bella.parse_minutes("1min"), 1.0)
        self.assertEqual(bella.parse_minutes("0min"), 0.0)
        self.assertEqual(bella.parse_minutes("5m/day"), 5.0)
        self.assertEqual(bella.parse_minutes("<1m/day"), 0.5)
        self.assertEqual(bella.parse_minutes("18hr 57min/day"), 1137.0)
        self.assertIsNone(bella.parse_minutes(None))

    def test_parse_health_trends_extracts_event_counts_per_behavior(self):
        out = bella.parse_health_trends(TRENDS_RESPONSE)
        self.assertEqual(out["eating_events"], 3.0)
        self.assertEqual(out["drinking_events"], 5.0)
        self.assertEqual(out["licking_events"], 2.0)
        self.assertEqual(out["activity_steps"], 325.0)
        self.assertEqual(out["rest_min"], 419.0)

    def test_parse_health_trends_records_zero_for_no_event_behaviors(self):
        """A behavior present but with null summary = zero events today, not missing."""
        out = bella.parse_health_trends(TRENDS_RESPONSE)
        self.assertEqual(out["barking_events"], 0.0)
        self.assertEqual(out["scratching_events"], 0.0)

    def test_parse_health_trends_skips_disabled_behaviors(self):
        resp = {"data": {"getPetHealthTrendsForPet": {"period": "DAY",
            "genericTrends": [], "behaviorTrends": [
                {"title": "Barking", "disabled": True, "summaryComponents": None}]}}}
        self.assertNotIn("barking_events", bella.parse_health_trends(resp))


class ProfileTests(unittest.TestCase):
    def test_profile_computes_age_breed_weight(self):
        p = bella.parse_profile(PROFILE_RESPONSE, TODAY)
        self.assertEqual(p["name"], "Bella")
        self.assertEqual(p["breed"], "Labrador Retriever")
        self.assertEqual(p["sex"], "female")
        self.assertEqual(p["age_years"], 4)            # born 2022-05-10, today 2026-06-12
        self.assertEqual(p["weight_lbs"], 65)          # 29.48 kg
        self.assertIn("adult", p["life_stage"])

    def test_profile_color_defaults_to_chocolate(self):
        p = bella.parse_profile(PROFILE_RESPONSE, TODAY)
        self.assertEqual(p["color"], "chocolate")

    def test_profile_color_env_override(self):
        p = bella.parse_profile(PROFILE_RESPONSE, TODAY, color="black")
        self.assertEqual(p["color"], "black")

    def test_life_stage_senior_for_old_lab(self):
        old = {"data": {"pet": {"name": "Bella", "gender": "FEMALE", "weight": 29.0,
               "yearOfBirth": 2017, "monthOfBirth": 1, "dayOfBirth": 1,
               "breed": {"name": "Labrador Retriever"}}}}
        self.assertIn("senior", bella.parse_profile(old, TODAY)["life_stage"])

    def test_profile_save_and_load_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "profile.json"
            p = bella.parse_profile(PROFILE_RESPONSE, TODAY)
            bella.save_profile(path, p)
            self.assertEqual(bella.load_profile(path)["age_years"], 4)

    def test_load_profile_missing_returns_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(bella.load_profile(Path(tmp) / "nope.json"))


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
    def test_no_credentials_omits_section(self):
        """No Fi creds → no new data → the section is dropped (None), not a
        placeholder line."""
        out = bella.build_section(TODAY, env={}, history_path=Path("/nonexistent"))
        self.assertIsNone(out)

    def test_build_section_with_injected_gql(self):
        responses = {"pets": PETS_RESPONSE, "steps": STEPS_RESPONSE,
                     "rest": REST_RESPONSE, "trends": TRENDS_RESPONSE,
                     "profile": PROFILE_RESPONSE}

        def fake_gql(kind, **kw):
            return responses[kind]

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "hist.json"
            prof = Path(tmp) / "prof.json"
            # seed enough history that a trend is readable
            for i in range(17):
                d = date(2026, 5, 26 + i) if 26 + i <= 31 else date(2026, 6, 26 + i - 31)
                bella.update_history(path, "steps", d.isoformat(), 8000.0)
            out = bella.build_section(TODAY, env={"FI_EMAIL": "x", "FI_PASSWORD": "y"},
                                      history_path=path, profile_path=prof,
                                      gql=fake_gql, pet_name="Bella")
            self.assertEqual(bella.load_profile(prof)["age_years"], 4)
            self.assertIn("🐕 Bella", out)
            self.assertIn("Activity:", out)
            self.assertIn("Sleep:", out)
            # today's live reading landed in the history file
            self.assertEqual(bella.load_history(path)["steps"][TODAY.isoformat()], 8421.0)
            # behavior counts captured into history too
            hist = bella.load_history(path)
            self.assertEqual(hist["eating_events"][TODAY.isoformat()], 3.0)
            self.assertEqual(hist["drinking_events"][TODAY.isoformat()], 5.0)

    def test_sleep_feed_merges_into_history_for_depth(self):
        """Fi's rest feed only returns the last few days; runs must accumulate
        them so trends become readable after a week."""
        responses = {"pets": PETS_RESPONSE, "steps": STEPS_RESPONSE, "rest": REST_RESPONSE}

        def fake_gql(kind, **kw):
            return responses[kind]

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "hist.json"
            # ten days of prior runs already accumulated
            for i in range(1, 11):
                bella.update_history(path, "sleep", f"2026-06-{i:02d}", 700.0)
            bella.build_section(TODAY, env={"FI_EMAIL": "x", "FI_PASSWORD": "y"},
                                history_path=path, gql=fake_gql, pet_name="Bella")
            sleep = bella.load_history(path)["sleep"]
            self.assertEqual(len(sleep), 12)  # 10 prior + Jun 11 + Jun 12 from the feed
            self.assertEqual(sleep["2026-06-12"], (28800 + 3600) / 60)

    def test_fi_error_omits_section_not_raises(self):
        """A fetch failure must not raise; with no new data, the section drops."""
        def boom(kind, **kw):
            raise OSError("fi down")

        out = bella.build_section(TODAY, env={"FI_EMAIL": "x", "FI_PASSWORD": "y"},
                                  history_path=Path("/nonexistent/h.json"), gql=boom)
        self.assertIsNone(out)

    def test_frozen_feed_omits_section(self):
        """Collar synced days ago and nothing new today → section dropped."""
        responses = {"pets": PETS_RESPONSE,
                     "steps": {"data": {"pet": {}}},          # no current steps
                     "rest": {"data": {"pet": {"restSummaryFeed": {"restSummaries": []}}}},
                     "trends": {"data": {"getPetHealthTrendsForPet": {
                         "period": "DAY", "genericTrends": [], "behaviorTrends": []}}},
                     "profile": PROFILE_RESPONSE}

        def fake_gql(kind, **kw):
            return responses[kind]

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "hist.json"
            # only old readings on file — newest is 5 days before today
            for i in range(5):
                bella.update_history(path, "steps", f"2026-06-0{3 + i}", 8000.0)
            out = bella.build_section(TODAY, env={"FI_EMAIL": "x", "FI_PASSWORD": "y"},
                                      history_path=path, profile_path=Path(tmp) / "p.json",
                                      gql=fake_gql, pet_name="Bella")
            self.assertIsNone(out)


class GistHistoryStoreTests(unittest.TestCase):
    """Durable (gist-backed) history so steps/behavior trends survive an
    ephemeral sandbox that wipes the local file each run."""

    def test_gist_load_parses_file_content(self):
        payload = {"files": {bella.GIST_FILE: {"content": json.dumps({"steps": {"2026-06-12": 14398.0}})}}}
        with mock.patch.object(bella.urllib.request, "urlopen",
                               return_value=_FakeResp(json.dumps(payload))):
            self.assertEqual(bella.gist_load("gid", "tok"), {"steps": {"2026-06-12": 14398.0}})

    def test_gist_load_missing_file_is_empty(self):
        with mock.patch.object(bella.urllib.request, "urlopen",
                               return_value=_FakeResp(json.dumps({"files": {}}))):
            self.assertEqual(bella.gist_load("gid", "tok"), {})

    def test_gist_save_sends_patch_with_history(self):
        seen = {}

        def fake_urlopen(req, timeout=30.0):
            seen["method"] = req.get_method()
            seen["url"] = req.full_url
            seen["body"] = json.loads(req.data.decode())
            return _FakeResp("{}")

        with mock.patch.object(bella.urllib.request, "urlopen", side_effect=fake_urlopen):
            bella.gist_save("gid", "tok", {"steps": {"2026-06-12": 1.0}})
        self.assertEqual(seen["method"], "PATCH")
        self.assertIn("gists/gid", seen["url"])
        content = seen["body"]["files"][bella.GIST_FILE]["content"]
        self.assertEqual(json.loads(content), {"steps": {"2026-06-12": 1.0}})

    def test_history_store_uses_gist_when_configured(self):
        env = {"BELLA_HISTORY_GIST": "gid", "GITHUB_TOKEN": "tok"}
        with mock.patch.object(bella, "gist_load", return_value={"x": 1}) as gl, \
                mock.patch.object(bella, "gist_save") as gs:
            load, save = bella._history_store(env, Path("/nope"))
            self.assertEqual(load(), {"x": 1})
            save({"y": 2})
            gl.assert_called_once()
            gs.assert_called_once()

    def test_history_store_falls_back_to_local_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "h.json"
            load, save = bella._history_store({}, path)
            save({"steps": {"2026-06-12": 9.0}})
            self.assertEqual(load(), {"steps": {"2026-06-12": 9.0}})

    def test_gist_store_failure_does_not_sink_section(self):
        env = {"BELLA_HISTORY_GIST": "gid", "GITHUB_TOKEN": "tok"}
        with mock.patch.object(bella, "gist_load", side_effect=RuntimeError("502")), \
                mock.patch.object(bella, "gist_save", side_effect=RuntimeError("502")):
            load, save = bella._history_store(env, Path("/nope"))
            self.assertEqual(load(), {})   # load degrades to empty
            save({"y": 2})                 # save swallows the error

    def test_build_section_round_trips_through_gist(self):
        responses = {"pets": PETS_RESPONSE, "steps": STEPS_RESPONSE,
                     "rest": REST_RESPONSE, "trends": TRENDS_RESPONSE,
                     "profile": PROFILE_RESPONSE}

        def fake_gql(kind, **kw):
            return responses[kind]

        # Seed the gist with 17 prior days so a trend is readable; build_section
        # must add today's reading and save the merged history back to the gist.
        seed = {"steps": {(TODAY - timedelta(days=i)).isoformat(): 8000.0 for i in range(1, 18)}}
        saved = {}
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.object(bella, "gist_load", return_value=json.loads(json.dumps(seed))), \
                mock.patch.object(bella, "gist_save", side_effect=lambda g, t, h: saved.update(h)):
            out = bella.build_section(
                TODAY,
                env={"FI_EMAIL": "x", "FI_PASSWORD": "y",
                     "BELLA_HISTORY_GIST": "gid", "GITHUB_TOKEN": "tok"},
                profile_path=Path(tmp) / "p.json", gql=fake_gql, pet_name="Bella")
        self.assertIn("🐕 Bella", out)
        self.assertEqual(saved["steps"][TODAY.isoformat()], 8421.0)   # today's reading persisted
        self.assertEqual(saved["eating_events"][TODAY.isoformat()], 3.0)


if __name__ == "__main__":
    unittest.main()
