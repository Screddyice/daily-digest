"""Unit tests for the Call Retro end-of-day digest. Stdlib only:

    python3 -m unittest test_retro -v

Network is injected. Covers NEBOS meeting decoding (JSON + SSE), the two date
formats NEBOS emits, today-window filtering, summary/next-steps parsing of both
prose and bulleted summaries, the Calls render, composition, and graceful
degradation.
"""
import json
import unittest
from datetime import date

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


if __name__ == "__main__":
    unittest.main()
