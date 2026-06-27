"""Unit tests for the meetings section. Stdlib only:

    python3 -m unittest test_meetings -v

The spec: at the end of the digest, list today's meetings — each with the
time + title and one concise context bullet: who it's with and (when the
event has a usable description) what it's for. Degrades explicitly, never
sinks the digest.
"""
import unittest
from datetime import date

import meetings

TODAY = date(2026, 6, 12)


def _ev(summary="Rivus Phase 2 sync", start="2026-06-12T09:00:00-07:00",
        attendees=None, description=None, status="confirmed", **extra):
    ev = {"summary": summary, "status": status, "start": {"dateTime": start}}
    if attendees is not None:
        ev["attendees"] = attendees
    if description is not None:
        ev["description"] = description
    ev.update(extra)
    return ev


class ParseEventTests(unittest.TestCase):
    def test_time_rendered_in_pacific(self):
        m = meetings.parse_event(_ev(start="2026-06-12T16:00:00+00:00"))
        self.assertEqual(m["when"], "9:00 AM")

    def test_all_day_event(self):
        m = meetings.parse_event({"summary": "Offsite", "status": "confirmed",
                                  "start": {"date": "2026-06-12"}})
        self.assertEqual(m["when"], "All day")

    def test_guests_prefer_display_name_and_prettify_emails(self):
        m = meetings.parse_event(_ev(attendees=[
            {"email": "shawn@teamnebula.ai", "self": True},
            {"email": "andres.gutierrez@rivus.com"},
            {"email": "x@y.com", "displayName": "Carlos Mejia"},
            {"email": "room-3@resource.calendar.google.com", "resource": True},
        ]))
        self.assertEqual(m["who"], ["Andres Gutierrez", "Carlos Mejia"])

    def test_purpose_skips_video_boilerplate_and_links(self):
        m = meetings.parse_event(_ev(description=(
            "Join Zoom Meeting\nhttps://zoom.us/j/123456\nPasscode: 998877\n"
            "Walk through the Phase 2 SOW and agree next steps.")))
        self.assertEqual(m["purpose"],
                         "Walk through the Phase 2 SOW and agree next steps.")

    def test_purpose_strips_html_and_truncates(self):
        long = "<p>Review the " + "very " * 40 + "long agenda.</p>"
        m = meetings.parse_event(_ev(description=long))
        self.assertNotIn("<", m["purpose"])
        self.assertLessEqual(len(m["purpose"]), meetings.PURPOSE_MAX_CHARS + 1)
        self.assertTrue(m["purpose"].endswith("…"))

    def test_no_description_means_no_purpose(self):
        self.assertIsNone(meetings.parse_event(_ev())["purpose"])

    def test_calendly_field_labels_are_not_a_purpose(self):
        """Calendly descriptions open with bare field labels — seen live as a
        useless '• Event Name' bullet. Labels must be skipped."""
        m = meetings.parse_event(_ev(description=(
            "Event Name\n30 Minute Meeting\nLocation\nGoogle Meet\n"
            "Discuss the pilot rollout plan.")))
        self.assertEqual(m["purpose"], "Discuss the pilot rollout plan.")


class RenderSectionTests(unittest.TestCase):
    def test_meeting_renders_time_title_and_one_context_bullet(self):
        out = meetings.render_section([meetings.parse_event(_ev(
            attendees=[{"email": "andres@rivus.com", "displayName": "Andres"}],
            description="Phase 2 SOW review."))])
        self.assertIn("📅 Meetings", out)
        self.assertIn("9:00 AM — Rivus Phase 2 sync", out)
        # who + what-for live on a single combined bullet
        self.assertIn("• With Andres — Phase 2 SOW review.", out)
        bullets = [l for l in out.splitlines() if l.strip().startswith("•")]
        self.assertEqual(len(bullets), 1)

    def test_context_bullet_is_just_who_when_no_purpose(self):
        out = meetings.render_section([meetings.parse_event(_ev(
            attendees=[{"email": "andres@rivus.com", "displayName": "Andres"}]))])
        self.assertIn("• With Andres", out)
        self.assertNotIn(" — ", out.split("Rivus Phase 2 sync", 1)[1])  # no dangling dash

    def test_solo_event_says_just_you(self):
        out = meetings.render_section([meetings.parse_event(_ev())])
        self.assertIn("• Just you", out)

    def test_many_guests_capped_with_count(self):
        guests = [{"email": f"p{i}@x.com", "displayName": f"Person {i}"}
                  for i in range(6)]
        out = meetings.render_section([meetings.parse_event(_ev(attendees=guests))])
        with_line = next(l for l in out.splitlines() if "With" in l)
        self.assertIn("more", with_line)

    def test_empty_day_reads_clear(self):
        out = meetings.render_section([])
        self.assertIn("Nothing scheduled", out)


class BuildSectionTests(unittest.TestCase):
    def _payload(self, *events):
        return {"data": {"response_data": {"items": list(events)}}}

    def test_unconfigured_env_omits_section(self):
        self.assertEqual(meetings.build_section(TODAY, env={}), "")

    def test_fetch_failure_degrades_explicitly(self):
        def boom(slug, args):
            raise RuntimeError("composio down")
        out = meetings.build_section(
            TODAY, env={"NEB_COMPOSIO_MCP_API_KEY": "k"}, call=boom)
        self.assertIn("📅 Meetings", out)
        self.assertIn("unavailable", out.lower())

    def test_happy_path_lists_meetings(self):
        payload = self._payload(
            _ev(),
            _ev(summary="TRC standup", start="2026-06-12T13:00:00-07:00"))
        out = meetings.build_section(
            TODAY, env={"NEB_COMPOSIO_MCP_API_KEY": "k"},
            call=lambda slug, args: payload)
        self.assertIn("Rivus Phase 2 sync", out)
        self.assertIn("TRC standup", out)

    def test_cancelled_and_self_declined_filtered(self):
        payload = self._payload(
            _ev(summary="Ghost", status="cancelled"),
            _ev(summary="Declined", attendees=[
                {"email": "me@x.com", "self": True, "responseStatus": "declined"}]),
            _ev(summary="Real one"))
        out = meetings.build_section(
            TODAY, env={"NEB_COMPOSIO_MCP_API_KEY": "k"},
            call=lambda slug, args: payload)
        self.assertNotIn("Ghost", out)
        self.assertNotIn("Declined", out)
        self.assertIn("Real one", out)


if __name__ == "__main__":
    unittest.main()
