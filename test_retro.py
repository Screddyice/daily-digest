"""Unit tests for the Call Retro end-of-day digest. Stdlib only:

    python3 -m unittest test_retro -v

Network is injected. Covers NEBOS meeting decoding (JSON + SSE), the two date
formats NEBOS emits, today-window filtering, summary/next-steps parsing of both
prose and bulleted summaries, the Calls render, composition, and graceful
degradation.
"""
import contextlib
import io
import json
import unittest
from datetime import date
from unittest import mock

import retro

TODAY = date(2026, 6, 27)

# A bulleted NEBOS summary with an explicit follow-up bullet.
BULLET_SUMMARY = (
    "- **Sales Pipeline:** Four-stage CRM manages 117 active leads.\n"
    "- **Pricing Workflow:** 1,265 quotes tracked with cost analytics.\n"
    "- **Next Steps & Collaboration:** Data sharing and architecture planning underway."
)
# A prose summary (no bullets) — VoltTruck-style.
PROSE_SUMMARY = ("VoltTruck wants AI on the sales side. They requested a phased "
                 "tiered proposal and a deck for the commercial director.")


def meeting(title, date_field, summary, attendees):
    return {"title": title, "date": date_field, "summary": summary, "attendees": attendees}


# Jun 27 / Jun 24 in both NEBOS date shapes.
JUN27_ISO = "2026-06-27T18:09:00.000Z"
JUN24_ISO = "2026-06-24T18:09:00.000Z"

DISCOVERY = meeting("Discovery (Carlos)", JUN27_ISO, BULLET_SUMMARY,
                    ["maira@teamnebula.ai", "shawn@teamnebula.ai", "jorge.castro@rivus.mx"])
VOLT = meeting("VoltTruck Discovery", JUN27_ISO, PROSE_SUMMARY,
               ["shawn@teamnebula.ai", "monica@volttruck.com"])
STALE = meeting("Old Standup", JUN24_ISO, "- **X:** y", ["shawn@teamnebula.ai"])


def mcp_envelope(payload) -> str:
    """Wrap a tool payload the way NEBOS does (JSON-RPC, text-wrapped)."""
    return json.dumps({"jsonrpc": "2.0", "id": "1",
                       "result": {"content": [{"type": "text", "text": json.dumps(payload)}]}})


class DecodeTests(unittest.TestCase):
    def test_decode_plain_json(self):
        self.assertEqual(retro._decode_mcp(mcp_envelope([{"a": 1}])), [{"a": 1}])

    def test_decode_sse(self):
        sse = "event: message\ndata: " + mcp_envelope([{"a": 2}]) + "\n\n"
        self.assertEqual(retro._decode_mcp(sse), [{"a": 2}])

    def test_decode_garbage_is_none(self):
        self.assertIsNone(retro._decode_mcp("not json at all"))


class DateTests(unittest.TestCase):
    def test_iso_string_parsed(self):
        dt = retro.meeting_dt(JUN24_ISO)
        self.assertIsNotNone(dt)
        self.assertEqual(dt.date(), date(2026, 6, 24))

    def test_seconds_object_parsed(self):
        dt = retro.meeting_dt({"_seconds": 1782493200})  # mid-2026 UTC
        self.assertIsNotNone(dt)

    def test_bad_date_is_none(self):
        self.assertIsNone(retro.meeting_dt("nonsense"))
        self.assertIsNone(retro.meeting_dt({}))
        self.assertIsNone(retro.meeting_dt(None))


class ParseTests(unittest.TestCase):
    def test_bulleted_summary_splits_bullets(self):
        b = retro.summary_bullets(BULLET_SUMMARY)
        self.assertEqual(len(b), 3)
        self.assertTrue(b[0].startswith("**Sales Pipeline"))

    def test_prose_summary_is_single_element(self):
        b = retro.summary_bullets(PROSE_SUMMARY)
        self.assertEqual(len(b), 1)

    def test_next_steps_extracted_from_label(self):
        rest, nxt = retro.split_next_steps(retro.summary_bullets(BULLET_SUMMARY))
        self.assertEqual(len(rest), 2)
        self.assertIsNotNone(nxt)
        self.assertIn("Data sharing", nxt)

    def test_no_next_steps_returns_none(self):
        rest, nxt = retro.split_next_steps(retro.summary_bullets(PROSE_SUMMARY))
        self.assertIsNone(nxt)
        self.assertEqual(len(rest), 1)

    def test_partners_external_only(self):
        self.assertEqual(retro.call_partners(DISCOVERY["attendees"]), "Rivus")
        self.assertEqual(retro.call_partners(["shawn@teamnebula.ai", "x@aiadvantageagency.co"]), "")


class RenderTests(unittest.TestCase):
    def test_one_call_has_title_time_summary_next(self):
        m = {**DISCOVERY, "_dt": retro.meeting_dt(JUN27_ISO)}
        out = retro.render_one_call(m)
        self.assertIn("*Discovery (Carlos)*", out)
        self.assertIn("Rivus", out)
        self.assertIn("• ", out)
        self.assertIn("• Next:", out)

    def test_prose_call_renders_summary_no_next(self):
        m = {**VOLT, "_dt": retro.meeting_dt(JUN27_ISO)}
        out = retro.render_one_call(m)
        self.assertIn("VoltTruck", out)
        self.assertNotIn("• Next:", out)

    def test_calls_section_caps_and_overflows(self):
        many = [{**VOLT, "_dt": retro.meeting_dt(JUN27_ISO)} for _ in range(retro.CALLS_MAX + 2)]
        out = retro.render_calls_section(many)
        self.assertIn("📞 Calls", out)
        self.assertIn("+2 more calls today", out)


class BuildSectionTests(unittest.TestCase):
    def _call(self, meetings):
        return lambda tool, args: meetings

    def test_no_token_returns_none(self):
        self.assertIsNone(retro.build_calls_section(TODAY, env={}))

    def test_today_filter_drops_stale(self):
        out = retro.build_calls_section(TODAY, env={}, call=self._call([DISCOVERY, STALE]))
        self.assertIn("Discovery (Carlos)", out)
        self.assertNotIn("Old Standup", out)

    def test_no_calls_today_returns_none(self):
        self.assertIsNone(retro.build_calls_section(TODAY, env={}, call=self._call([STALE])))

    def test_fetch_error_degrades_to_none(self):
        def boom(tool, args):
            raise OSError("nebos down")
        self.assertIsNone(retro.build_calls_section(TODAY, env={}, call=boom))

    def test_non_list_payload_returns_none(self):
        self.assertIsNone(retro.build_calls_section(TODAY, env={}, call=lambda t, a: {"error": "x"}))


class TodayTests(unittest.TestCase):
    def test_retro_date_override(self):
        import os
        os.environ["RETRO_DATE"] = "2026-06-24"
        try:
            self.assertEqual(retro._today(), date(2026, 6, 24))
        finally:
            del os.environ["RETRO_DATE"]

    def test_bad_retro_date_falls_back_to_now(self):
        import os
        os.environ["RETRO_DATE"] = "not-a-date"
        try:
            self.assertIsInstance(retro._today(), date)
        finally:
            del os.environ["RETRO_DATE"]


class CallRecordTests(unittest.TestCase):
    def test_record_fields(self):
        m = {**DISCOVERY, "_dt": retro.meeting_dt(JUN27_ISO)}
        r = retro.call_record(m)
        self.assertEqual(r["title"], "Discovery (Carlos)")
        self.assertEqual(r["who"], "Rivus")
        self.assertTrue(r["label_line"].startswith("*Discovery (Carlos)*"))
        self.assertTrue(r["summary"])
        self.assertIn("Data sharing", r["next_steps"])

    def test_prose_record_has_no_next_steps(self):
        r = retro.call_record({**VOLT, "_dt": retro.meeting_dt(JUN27_ISO)})
        self.assertEqual(r["next_steps"], "")
        self.assertTrue(r["summary"])

    def test_section_bullets_extracts_only_bullets(self):
        section = "🎯 Top 5 — NEBOS\n\n• [TMNS-82] Follow up (High)\n• Reply to X — y"
        self.assertEqual(retro._section_bullets(section),
                         ["• [TMNS-82] Follow up (High)", "• Reply to X — y"])
        self.assertEqual(retro._section_bullets(None), [])


class BuildDataTests(unittest.TestCase):
    def _call(self, meetings):
        return lambda tool, args: meetings

    def test_data_shape_and_counts(self):
        data = retro.build_retro_data(
            TODAY, env={}, call=self._call([DISCOVERY, VOLT, STALE]),
            nebos_section="🎯 Top 5 — NEBOS\n\n• Reply to Luis (Rivus) — Carlos")
        self.assertEqual(data["label"], "Saturday, June 27")
        self.assertEqual(data["call_count"], 2)              # STALE (Jun 24) filtered out
        self.assertEqual(data["top5"], ["• Reply to Luis (Rivus) — Carlos"])
        self.assertEqual({c["title"] for c in data["calls"]},
                         {"Discovery (Carlos)", "VoltTruck Discovery"})

    def test_no_token_no_calls(self):
        data = retro.build_retro_data(TODAY, env={}, nebos_section=None)
        self.assertEqual(data["call_count"], 0)
        self.assertEqual(data["calls"], [])
        self.assertEqual(data["top5"], [])


class BuildRetroTests(unittest.TestCase):
    def test_retro_leads_with_nebos_then_calls(self):
        out = retro.build_retro(
            TODAY,
            nebos_section="🎯 Top 5 — NEBOS\n\n• [TMNS-82] Follow up (High)",
            calls_section="📞 Calls\n\n*Discovery* (1:00 PM)")
        self.assertIn("🌙 Call Retro — Saturday, June 27, 2026", out)
        self.assertLess(out.index("🎯 Top 5 — NEBOS"), out.index("📞 Calls"))

    def test_empty_parts_are_dropped(self):
        out = retro.build_retro(TODAY, nebos_section=None, calls_section=None)
        self.assertIn("🌙 Call Retro", out)
        self.assertNotIn("🎯", out)
        self.assertNotIn("📞", out)


# Pending action items carried into the morning digest. Jun 27 = Saturday.
NS_SHAWN = meeting(
    "Carlos Sync", JUN27_ISO,
    "- **Recap:** Reviewed the pipeline.\n- **Next steps:** Shawn to send the revised SOW to Andres.",
    ["shawn@teamnebula.ai", "jorge@rivus.mx"])
NS_OTHER = meeting(
    "Volt Sync", "2026-06-26T18:00:00.000Z",
    "- **Recap:** Demoed the tool.\n- **Next steps:** Maira to prep the commercial deck.",
    ["shawn@teamnebula.ai", "monica@volttruck.com"])
NS_NONE = meeting("Standup", JUN27_ISO, "- **Recap:** Status only, nothing open.",
                  ["shawn@teamnebula.ai"])
# Jun 23 is outside the default 3-day lookback (window = Jun 25..27).
NS_OLD = meeting("Old Call", "2026-06-23T18:00:00.000Z",
                 "- **Next steps:** Shawn to do the old thing.", ["shawn@teamnebula.ai"])


class PendingSectionTests(unittest.TestCase):
    def test_day_label(self):
        self.assertEqual(retro._day_label(TODAY, TODAY), "today")
        self.assertEqual(retro._day_label(date(2026, 6, 26), TODAY), "yesterday")
        self.assertEqual(retro._day_label(date(2026, 6, 25), TODAY), "Thursday")

    def test_shawns_items_lead_and_others_follow(self):
        call = lambda tool, args: [NS_OTHER, NS_SHAWN, NS_NONE]
        out = retro.build_pending_section(today=TODAY, call=call)
        self.assertIn("📋 Still pending", out)
        self.assertIn("Shawn to send the revised SOW", out)
        self.assertIn("Maira to prep the commercial deck", out)
        # Shawn's open item leads, regardless of call recency.
        self.assertLess(out.index("revised SOW"), out.index("Maira"))
        self.assertIn("(from Carlos Sync, today)", out)
        self.assertIn("(from Volt Sync, yesterday)", out)

    def test_calls_without_next_steps_are_skipped(self):
        call = lambda tool, args: [NS_NONE]
        self.assertIsNone(retro.build_pending_section(today=TODAY, call=call))

    def test_window_excludes_older_calls(self):
        call = lambda tool, args: [NS_OLD]            # Jun 23, outside 3-day window
        self.assertIsNone(retro.build_pending_section(today=TODAY, call=call))

    def test_none_without_nebos_token(self):
        self.assertIsNone(retro.build_pending_section(today=TODAY, env={}))

    def test_overflow_collapses_to_more_line(self):
        many = [meeting(f"Call {i}", JUN27_ISO,
                        f"- **Next steps:** Shawn to handle item {i}.",
                        ["shawn@teamnebula.ai"]) for i in range(retro.PENDING_MAX_ITEMS + 2)]
        out = retro.build_pending_section(today=TODAY, call=lambda t, a: many)
        self.assertIn("more open item", out)


class DegradeTests(unittest.TestCase):
    """The --json payload must never crash and must flag when a source degraded,
    so the routine retries (or says so honestly) instead of a misleading empty
    retro. Regression guard for the Jul 1 2026 'data unavailable' incident."""

    def test_healthy_path_not_degraded(self):
        data = retro.build_retro_data(
            TODAY, env={}, call=lambda t, a: [DISCOVERY],
            nebos_section="🎯 Top 5 — NEBOS\n\n• Reply to X — y")
        self.assertFalse(data["degraded"])
        self.assertEqual(data["call_count"], 1)

    def test_meeting_fetch_raise_degrades_not_crash(self):
        def boom(tool, args):
            raise OSError("nebos down")
        data = retro.build_retro_data(TODAY, env={}, call=boom, nebos_section=None)
        self.assertTrue(data["degraded"])
        self.assertEqual(data["call_count"], 0)
        self.assertEqual(data["calls"], [])

    def test_top5_build_raise_degrades_not_crash(self):
        with mock.patch.object(retro.nebos, "build_section",
                               side_effect=RuntimeError("composio down")):
            data = retro.build_retro_data(
                TODAY, env={"NEBOS_MCP_TOKEN": "x"}, call=lambda t, a: [DISCOVERY])
        self.assertTrue(data["degraded"])
        self.assertEqual(data["top5"], [])
        self.assertEqual(data["call_count"], 1)   # meetings still render

    def test_bad_meeting_is_skipped_not_fatal(self):
        with mock.patch.object(retro, "call_record", side_effect=ValueError("boom")):
            data = retro.build_retro_data(
                TODAY, env={}, call=lambda t, a: [DISCOVERY], nebos_section=None)
        self.assertTrue(data["degraded"])
        self.assertEqual(data["calls"], [])

    def test_json_output_always_valid_even_when_build_crashes(self):
        with mock.patch.object(retro.sys, "argv", ["retro.py", "--json"]), \
             mock.patch.object(retro, "build_retro_data",
                               side_effect=RuntimeError("kaboom")), \
             mock.patch.object(retro, "_today", return_value=TODAY):
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                rc = retro.main()
        self.assertEqual(rc, 0)
        payload = json.loads(buf.getvalue())      # must be parseable JSON
        self.assertTrue(payload["degraded"])
        self.assertEqual(payload["call_count"], 0)
        self.assertEqual(payload["calls"], [])


if __name__ == "__main__":
    unittest.main()
