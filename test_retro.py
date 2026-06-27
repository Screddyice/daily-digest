"""Unit tests for the Call Retro end-of-day digest. Stdlib only:

    python3 -m unittest test_retro -v

Network is injected. Covers Gemini-notes parsing (summary + action items),
the Call section render, composition, and graceful degradation.
"""
import unittest
from datetime import date

import retro

TODAY = date(2026, 6, 27)

NOTES_TEXT = """Daily Standup
Invited @Ian Kiku @Shawn Reddy
Meeting records Transcript
Summary
The team reviewed client project status and financial milestones while refining technical infrastructure.

Standardizing Workflow Processes
A 90-day sprint framework was adopted to track commitments.
Decisions
Aligned
Client meeting recap automation
Next steps
- [Ibrahim Zia] Send Design Options: Distribute the created design options to the team.
- [Shawn Reddy] Draft Client Proposal: Draft a proposal for the client.
- [The group] Reschedule Discussion: Move the OKR discussion to Thursday.
Details
Firebase project payment was discussed.
"""

DOC_NAME = "Daily Standup - 2026/06/26 10:45 PDT - Notes by Gemini"

FIND_RESPONSE = {"data": {"files": [
    {"id": "DOC1", "name": DOC_NAME, "modifiedTime": "2026-06-26T19:13:13Z"}]}}
DOC_RESPONSE = {"data": {"plain_text": NOTES_TEXT}}


class ParseTests(unittest.TestCase):
    def test_summary_is_first_line_under_header(self):
        s = retro.parse_summary(NOTES_TEXT)
        self.assertTrue(s.startswith("The team reviewed client project status"))
        self.assertLessEqual(len(s), retro.SUMMARY_MAX_CHARS + 1)

    def test_summary_missing_returns_none(self):
        self.assertIsNone(retro.parse_summary("No headers here\njust text"))

    def test_action_items_owner_and_task(self):
        items = retro.parse_action_items(NOTES_TEXT)
        self.assertEqual(items[0], ("Ibrahim", "Send Design Options"))
        self.assertEqual(items[1], ("Shawn", "Draft Client Proposal"))
        self.assertEqual(items[2], ("Team", "Reschedule Discussion"))  # "The group" -> Team

    def test_action_items_stop_at_next_section(self):
        # "Details" follows Next steps; its content must not leak in as an item
        items = retro.parse_action_items(NOTES_TEXT)
        self.assertEqual(len(items), 3)
        self.assertNotIn("Firebase", " ".join(t for _, t in items))


class RenderTests(unittest.TestCase):
    def test_render_has_label_summary_and_actions(self):
        out = retro.render_call_section(
            DOC_NAME, "Reviewed status and milestones.",
            [("Ibrahim", "Send Design Options"), ("Shawn", "Draft Client Proposal")])
        self.assertIn("📞 Daily Standup", out)                 # label off the doc name
        self.assertIn("• Summary: Reviewed status and milestones.", out)
        self.assertIn("• Action items: Ibrahim: Send Design Options; Shawn: Draft Client Proposal", out)

    def test_render_caps_action_items_with_overflow(self):
        items = [(f"P{i}", f"Task {i}") for i in range(9)]
        out = retro.render_call_section(DOC_NAME, "s", items)
        self.assertIn(f"+{9 - retro.ACTIONS_MAX_ITEMS} more", out)


class BuildSectionTests(unittest.TestCase):
    def _call(self, slug, args):
        return {retro.FIND_SLUG: FIND_RESPONSE, retro.DOC_SLUG: DOC_RESPONSE}[slug]

    def test_no_gateway_returns_none(self):
        self.assertIsNone(retro.build_call_section(TODAY, env={}))

    def test_call_section_end_to_end(self):
        out = retro.build_call_section(TODAY, env={}, call=self._call)
        self.assertIn("📞 Daily Standup", out)
        self.assertIn("Summary:", out)
        self.assertIn("Ibrahim: Send Design Options", out)

    def test_no_notes_returns_none(self):
        empty = lambda slug, args: {"data": {"files": []}}
        self.assertIsNone(retro.build_call_section(TODAY, env={}, call=empty))

    def test_fetch_error_degrades_to_none(self):
        def boom(slug, args):
            raise OSError("drive down")
        self.assertIsNone(retro.build_call_section(TODAY, env={}, call=boom))


class BuildRetroTests(unittest.TestCase):
    def test_retro_leads_with_nebos_then_call(self):
        out = retro.build_retro(
            TODAY,
            nebos_section="🎯 Top 5 — NEBOS\n\n• [TMNS-82] Follow up (High)",
            call_section="📞 Daily Standup\n\n• Summary: x\n• Action items: y")
        self.assertIn("🌙 Call Retro — Saturday, June 27, 2026", out)
        self.assertLess(out.index("🎯 Top 5 — NEBOS"), out.index("📞 Daily Standup"))

    def test_empty_parts_are_dropped(self):
        out = retro.build_retro(TODAY, nebos_section=None, call_section=None)
        self.assertIn("🌙 Call Retro", out)
        self.assertNotIn("🎯", out)
        self.assertNotIn("📞", out)


if __name__ == "__main__":
    unittest.main()
