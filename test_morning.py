"""Composition tests for the trends-only morning digest. Stdlib only:

    python3 -m unittest test_morning -v
"""
import re
import unittest
from datetime import date, timedelta
from unittest import mock

import morning

TODAY = date(2026, 6, 12)


def _series(end: date, n: int, val: float) -> dict:
    return {(end - timedelta(days=i)).isoformat(): val for i in range(n)}


def _you_data(end: date = TODAY) -> dict:
    # Mostly at-baseline, with ONE real deviation (sleep running short the last
    # few nights) so the You section has something worth surfacing. A fully
    # steady dataset now renders nothing — the digest drops it as filler.
    return {
        "step_count": _series(end, 17, 5000),
        "active_energy": _series(end, 17, 300),
        "apple_exercise_time": _series(end, 17, 25),
        "heart_rate_variability": _series(end, 17, 45),
        "resting_heart_rate": _series(end, 17, 60),
        "blood_oxygen_saturation": _series(end, 17, 97),
        "sleep_analysis": {**_series(end, 17, 7.5),
                           **{(end - timedelta(days=i)).isoformat(): 5.0 for i in range(3)}},
    }


MEETINGS = "📅 Meetings\n\n9:00 AM — Rivus sync\n  • With Andres"


class BuildDigestTests(unittest.TestCase):
    def test_digest_is_you_bella_then_meetings_last(self):
        out = morning.build_digest(TODAY, daily_by_metric=_you_data(),
                                   bella_section="🐕 Bella\n\n• Activity: steady.",
                                   meetings_section=MEETINGS, nebos_section="")
        self.assertIn("Morning Digest", out)
        self.assertIn("💪 You", out)
        self.assertIn("🐕 Bella", out)
        self.assertTrue(out.index("📅 Meetings") > out.index("🐕 Bella"))
        self.assertIn("9:00 AM — Rivus sync", out)
        self.assertNotIn("📵", out)   # fresh feed → no staleness note

    def test_empty_meetings_section_is_omitted(self):
        out = morning.build_digest(TODAY, daily_by_metric=_you_data(),
                                   bella_section="🐕 Bella\n\n• Activity: steady.",
                                   meetings_section="", nebos_section="")
        self.assertNotIn("Meetings", out)
        self.assertNotIn("📅", out)

    def test_you_section_stays_numberless_while_bella_may_carry_numbers(self):
        """The LLM-written (or deterministic) You section is numberless; Bella's
        section is allowed real numbers — they must survive into the digest."""
        out = morning.build_digest(
            TODAY, daily_by_metric=_you_data(),
            bella_section="🐕 Bella\n\n• Drinking: 14 events today, drinking about as usual.",
            llm_body=None, meetings_section="", nebos_section="")
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
            llm_body=llm_body, meetings_section=MEETINGS, nebos_section="")
        self.assertIn("Morning Digest", out)
        self.assertIn("• Activity: trending down.", out)   # You narrative verbatim
        self.assertIn("🐕 Bella", out)                      # Bella appended
        self.assertIn("14 events", out)                     # numbers survive
        self.assertTrue(out.index("🐕 Bella") > out.index("💪 You"))
        self.assertTrue(out.rstrip().endswith("• With Andres"))

    def test_falls_back_to_deterministic_when_llm_body_none(self):
        out = morning.build_digest(TODAY, daily_by_metric=_you_data(),
                                   bella_section="🐕 Bella\n\n• Activity: steady.",
                                   llm_body=None, meetings_section=MEETINGS,
                                   nebos_section="")
        self.assertIn("💪 You", out)
        self.assertIn("🐕 Bella", out)
        self.assertIn("📅 Meetings", out)

    def test_stale_health_swaps_you_section_for_staleness_note(self):
        """Health feed frozen for days → no stale trends re-rendered, but the
        digest says WHY health is missing (📵 note with the last-data date)
        instead of dropping the section silently; Bella still shows."""
        stale = _you_data(TODAY - timedelta(days=3))
        out = morning.build_digest(TODAY, daily_by_metric=stale,
                                   bella_section="🐕 Bella\n\n• Activity: steady.",
                                   llm_body=None, meetings_section="", nebos_section="")
        self.assertNotIn("💪 You", out)
        self.assertIn("📵 No phone health data for 3 days (last data Jun 9)", out)
        self.assertIn("🐕 Bella", out)

    def test_no_health_data_shows_empty_note(self):
        out = morning.build_digest(TODAY, daily_by_metric={},
                                   bella_section="🐕 Bella\n\n• Activity: steady.",
                                   llm_body=None, meetings_section="", nebos_section="")
        self.assertNotIn("💪 You", out)
        self.assertIn("📵 No phone health data in the last 30 days", out)
        self.assertIn("🐕 Bella", out)

    def test_none_bella_section_is_omitted(self):
        """build_section returns None when the collar has no new data; that
        drops Bella from the digest (and must not trigger a re-fetch)."""
        out = morning.build_digest(TODAY, daily_by_metric=_you_data(),
                                   bella_section=None, llm_body=None,
                                   meetings_section="", nebos_section="")
        self.assertIn("💪 You", out)
        self.assertNotIn("🐕", out)

    def test_both_health_and_bella_omitted_leaves_meetings(self):
        out = morning.build_digest(TODAY, daily_by_metric={},
                                   bella_section=None, llm_body=None,
                                   meetings_section=MEETINGS, nebos_section="")
        self.assertNotIn("💪 You", out)
        self.assertNotIn("🐕", out)
        self.assertIn("📅 Meetings", out)
        self.assertIn("Morning Digest", out)

    def test_nebos_section_leads_the_digest(self):
        """The Top-5 NEBOS section, when present, sits above the You section."""
        nebos = "🎯 Top 5 — NEBOS\n\n• [TMNS-82] Follow up on Lockheed (High)"
        out = morning.build_digest(TODAY, daily_by_metric=_you_data(),
                                   bella_section="🐕 Bella\n\n• Activity: steady.",
                                   llm_body=None, meetings_section=MEETINGS,
                                   nebos_section=nebos)
        self.assertIn("🎯 Top 5 — NEBOS", out)
        self.assertTrue(out.index("🎯 Top 5 — NEBOS") < out.index("💪 You"))
        self.assertIn("[TMNS-82]", out)

    def test_empty_nebos_section_is_omitted(self):
        out = morning.build_digest(TODAY, daily_by_metric=_you_data(),
                                   bella_section="🐕 Bella\n\n• Activity: steady.",
                                   llm_body=None, meetings_section="", nebos_section="")
        self.assertNotIn("🎯", out)
        self.assertNotIn("NEBOS", out)


class PendingPlacementTests(unittest.TestCase):
    PENDING = "📋 Still pending\n\n• Send the revised SOW (from Carlos Sync, today)"

    def test_pending_sits_after_top5_and_before_you(self):
        nebos = "🎯 Top 5 — NEBOS\n\n• [TMNS-82] Follow up (High)"
        out = morning.build_digest(
            TODAY, daily_by_metric=_you_data(),
            bella_section="🐕 Bella\n\n• Activity: steady.", llm_body=None,
            meetings_section="", nebos_section=nebos, pending_section=self.PENDING)
        self.assertIn("📋 Still pending", out)
        self.assertLess(out.index("🎯 Top 5 — NEBOS"), out.index("📋 Still pending"))
        self.assertLess(out.index("📋 Still pending"), out.index("💪 You"))

    def test_none_pending_section_is_omitted(self):
        out = morning.build_digest(
            TODAY, daily_by_metric=_you_data(),
            bella_section="🐕 Bella\n\n• Activity: steady.", llm_body=None,
            meetings_section="", nebos_section="", pending_section=None)
        self.assertNotIn("Still pending", out)
        self.assertNotIn("📋", out)


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
        # Fully at-baseline health data — no deviations, so nothing to alert on.
        healthy = {m: _series(TODAY, 17, v) for m, v in (
            ("step_count", 5000), ("active_energy", 300), ("apple_exercise_time", 25),
            ("heart_rate_variability", 45), ("resting_heart_rate", 60),
            ("blood_oxygen_saturation", 97), ("sleep_analysis", 7.5),
        )}
        sent = []
        with tempfile.TemporaryDirectory() as tmp:
            morning.run_alerts(healthy, {"steps": {}, "sleep": {}}, TODAY,
                               send=lambda lines: sent.extend(lines),
                               state_path=Path(tmp) / "s.json")
        self.assertEqual(sent, [])


class MainAlertsGuardTests(unittest.TestCase):
    """main() must still POST the digest but SKIP the stateful Telegram alerts
    when DIGEST_NO_ALERTS is set (a stateless cloud-routine runtime)."""

    def _patches(self):
        return mock.patch.multiple(
            morning,
            _send_slack=mock.DEFAULT, _send_telegram=mock.DEFAULT,
            run_alerts=mock.DEFAULT)

    def test_digest_no_alerts_posts_but_skips_alerts(self):
        env = {"SLACK_BOT_TOKEN": "x", "SLACK_CHANNEL": "D0",
               "DIGEST_DELIVERY": "slack", "DIGEST_NO_ALERTS": "1"}
        with mock.patch.dict(morning.os.environ, env, clear=True), \
                mock.patch.object(morning.health, "fetch_daily_by_metric", return_value={}), \
                mock.patch.object(morning.bella, "build_section", return_value=None), \
                mock.patch.object(morning.nebos, "build_section", return_value=None), \
                mock.patch.object(morning.meetings, "build_section", return_value=None), \
                self._patches() as m:
            self.assertEqual(morning.main(), 0)
            m["_send_slack"].assert_called_once()
            m["run_alerts"].assert_not_called()

    def test_alerts_run_by_default(self):
        env = {"SLACK_BOT_TOKEN": "x", "SLACK_CHANNEL": "D0",
               "DIGEST_DELIVERY": "slack"}
        with mock.patch.dict(morning.os.environ, env, clear=True), \
                mock.patch.object(morning.health, "fetch_daily_by_metric", return_value={}), \
                mock.patch.object(morning.bella, "build_section", return_value=None), \
                mock.patch.object(morning.nebos, "build_section", return_value=None), \
                mock.patch.object(morning.meetings, "build_section", return_value=None), \
                self._patches() as m:
            self.assertEqual(morning.main(), 0)
            m["run_alerts"].assert_called_once()


class DeliveryRoutingTests(unittest.TestCase):
    """DIGEST_DELIVERY routes the digest: telegram (default) vs slack."""

    def _run_main(self, env):
        with mock.patch.dict(morning.os.environ, env, clear=True), \
                mock.patch.object(morning.health, "fetch_daily_by_metric", return_value={}), \
                mock.patch.object(morning.bella, "build_section", return_value=None), \
                mock.patch.object(morning.nebos, "build_section", return_value=None), \
                mock.patch.object(morning.meetings, "build_section", return_value=None), \
                mock.patch.multiple(morning, _send_slack=mock.DEFAULT,
                                    _send_telegram=mock.DEFAULT,
                                    run_alerts=mock.DEFAULT) as m:
            self.assertEqual(morning.main(), 0)
        return m

    def test_default_delivery_is_telegram(self):
        m = self._run_main({"SLACK_BOT_TOKEN": "x", "SLACK_CHANNEL": "D0"})
        m["_send_telegram"].assert_called_once()
        m["_send_slack"].assert_not_called()

    def test_slack_delivery_opt_in(self):
        m = self._run_main({"SLACK_BOT_TOKEN": "x", "SLACK_CHANNEL": "D0",
                            "DIGEST_DELIVERY": "slack"})
        m["_send_slack"].assert_called_once()
        m["_send_telegram"].assert_not_called()

    def test_send_telegram_invokes_hermes_cli(self):
        calls = []

        def fake_run(argv, **kwargs):
            calls.append(argv)
            return mock.Mock(returncode=0, stderr="")

        morning._send_telegram("digest body", run=fake_run)
        self.assertEqual(len(calls), 1)
        self.assertTrue(str(calls[0][0]).endswith("hermes"))
        self.assertEqual(calls[0][1:4], ["send", "-t", "telegram"])
        self.assertIn("digest body", calls[0])

    def test_send_telegram_failure_raises(self):
        def fake_run(argv, **kwargs):
            return mock.Mock(returncode=1, stderr="boom")

        with self.assertRaises(SystemExit):
            morning._send_telegram("digest body", run=fake_run)


if __name__ == "__main__":
    unittest.main()
