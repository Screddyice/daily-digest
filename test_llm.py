"""Unit tests for the LLM digest writer. Stdlib only:

    python3 -m unittest test_llm -v

The API call is injected; tests cover prompt construction, the no-digits
output gate, snapshot persistence, and graceful degradation to None (which
makes the caller fall back to the deterministic trends renderer).
"""
import json
import tempfile
import unittest
from datetime import date
from pathlib import Path

import llm

TODAY = date(2026, 6, 12)

CURRENT = {"you": {"step_count": {"2026-06-12": 4000.0}},
           "bella": {"steps": {"2026-06-12": 8000.0}}}
PREVIOUS = {"date": "2026-06-11",
            "you": {"step_count": {"2026-06-11": 6000.0}},
            "bella": {"steps": {"2026-06-11": 9000.0}}}

GOOD_OUTPUT = """💪 You

• Activity & exercise: trending down — less movement than your last export.
• Stress: no signs of stress.

🐕 Bella

• Activity: slightly less movement than before."""


class PromptTests(unittest.TestCase):
    def test_prompt_includes_both_exports_and_rules(self):
        system, user = llm.build_prompt(CURRENT, PREVIOUS, TODAY)
        self.assertIn("never", system.lower())
        self.assertIn("number", system.lower())
        self.assertIn("Bella", system)
        self.assertIn("2026-06-12", user)   # current export data
        self.assertIn("2026-06-11", user)   # previous export data

    def test_prompt_handles_missing_previous(self):
        system, user = llm.build_prompt(CURRENT, None, TODAY)
        self.assertIn("no previous export", user.lower())


class GenerateTests(unittest.TestCase):
    def test_returns_llm_text_when_clean(self):
        out = llm.generate_digest(CURRENT, PREVIOUS, TODAY, hermes=None,
                                  call=lambda s, u: GOOD_OUTPUT, api_key="sk-x")
        self.assertEqual(out, GOOD_OUTPUT)

    def test_rejects_output_containing_digits(self):
        dirty = "• Activity: down 23% from 6000 steps."
        out = llm.generate_digest(CURRENT, PREVIOUS, TODAY, hermes=None,
                                  call=lambda s, u: dirty, api_key="sk-x")
        self.assertIsNone(out)

    def test_returns_none_without_api_key(self):
        out = llm.generate_digest(CURRENT, PREVIOUS, TODAY, hermes=None,
                                  call=lambda s, u: GOOD_OUTPUT, api_key="")
        self.assertIsNone(out)

    def test_returns_none_on_api_error(self):
        def boom(s, u):
            raise OSError("api down")
        out = llm.generate_digest(CURRENT, PREVIOUS, TODAY, hermes=None,
                                  call=boom, api_key="sk-x")
        self.assertIsNone(out)


class HermesBackendTests(unittest.TestCase):
    def test_hermes_is_preferred_over_anthropic(self):
        out = llm.generate_digest(
            CURRENT, PREVIOUS, TODAY,
            hermes=lambda prompt: GOOD_OUTPUT,
            call=lambda s, u: "anthropic output", api_key="sk-x")
        self.assertEqual(out, GOOD_OUTPUT)

    def test_hermes_prompt_contains_both_exports(self):
        seen = {}

        def fake_hermes(prompt):
            seen["prompt"] = prompt
            return GOOD_OUTPUT

        llm.generate_digest(CURRENT, PREVIOUS, TODAY, hermes=fake_hermes, api_key="")
        self.assertIn("2026-06-12", seen["prompt"])
        self.assertIn("2026-06-11", seen["prompt"])

    def test_hermes_failure_falls_back_to_anthropic(self):
        def boom(prompt):
            raise OSError("hermes down")
        out = llm.generate_digest(CURRENT, PREVIOUS, TODAY, hermes=boom,
                                  call=lambda s, u: GOOD_OUTPUT, api_key="sk-x")
        self.assertEqual(out, GOOD_OUTPUT)

    def test_hermes_digit_output_falls_back_to_anthropic(self):
        out = llm.generate_digest(CURRENT, PREVIOUS, TODAY,
                                  hermes=lambda p: "down 23%",
                                  call=lambda s, u: GOOD_OUTPUT, api_key="sk-x")
        self.assertEqual(out, GOOD_OUTPUT)

    def test_all_backends_dirty_returns_none(self):
        out = llm.generate_digest(CURRENT, PREVIOUS, TODAY,
                                  hermes=lambda p: "down 23%",
                                  call=lambda s, u: "up 5%", api_key="sk-x")
        self.assertIsNone(out)


class SnapshotTests(unittest.TestCase):
    def test_save_then_load_previous_returns_older_snapshot(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            llm.save_snapshot(d, date(2026, 6, 11), PREVIOUS["you"], PREVIOUS["bella"])
            llm.save_snapshot(d, TODAY, CURRENT["you"], CURRENT["bella"])
            prev = llm.load_previous_snapshot(d, TODAY)
            self.assertEqual(prev["date"], "2026-06-11")
            self.assertIn("step_count", prev["you"])

    def test_same_day_rerun_ignores_todays_own_snapshot(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            llm.save_snapshot(d, TODAY, CURRENT["you"], CURRENT["bella"])
            self.assertIsNone(llm.load_previous_snapshot(d, TODAY))

    def test_snapshots_pruned_to_keep_limit(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            for i in range(1, 25):
                llm.save_snapshot(d, date(2026, 5, i), {}, {})
            files = sorted(d.glob("*.json"))
            self.assertLessEqual(len(files), llm.SNAPSHOT_KEEP)


if __name__ == "__main__":
    unittest.main()
