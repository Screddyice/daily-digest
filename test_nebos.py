"""Unit tests for the Top-5 (NEBOS) section. Stdlib only:

    python3 -m unittest test_nebos -v

Network is injected; these cover Linear/Gmail parsing, the client-email
filter (internal + automated senders dropped), urgency ranking, and graceful
degradation when the Composio gateway isn't configured.
"""
import unittest
from datetime import date, datetime
from pathlib import Path  # noqa: F401 (kept parallel to sibling test modules)

import nebos

TODAY = date(2026, 6, 27)


def _ms(d: date) -> str:
    return str(int(datetime(d.year, d.month, d.day, 12, 0).timestamp() * 1000))


# Composio wraps the GraphQL payload; _walk digs out the nodes regardless.
LINEAR_RESPONSE = {"data": {"data": {"viewer": {"assignedIssues": {"nodes": [
    {"identifier": "TMNS-82", "title": "Follow up on Lockheed Martin",
     "priority": 2, "priorityLabel": "High", "dueDate": "2026-06-19",
     "state": {"name": "Todo"}, "team": {"key": "TMNS"}},
    {"identifier": "TMN-926", "title": "chore: remove dead TRC server profile",
     "priority": 0, "priorityLabel": "No priority", "dueDate": None,
     "state": {"name": "In Progress"}, "team": {"key": "TMN"}},
]}}}}}


def _thread(frm, subject, when: date, labels):
    return {"messages": [{
        "id": "x", "internalDate": _ms(when), "labelIds": labels,
        "snippet": "…",
        "payload": {"headers": [{"name": "From", "value": frm},
                                {"name": "Subject", "value": subject}]},
    }]}


# Default allowlist seeds rivus.mx / newcalgon.net / rs21.io (client domains).
GMAIL_RESPONSE = {"data": {"threads": [
    _thread("Luis Patino <luis.patino@rivus.mx>",
            "Re: Re: Next steps for Carlos", date(2026, 6, 26),
            ["UNREAD", "IMPORTANT", "INBOX"]),                       # client, recent
    _thread("Angelica Killingsworth <angelicak@rs21.io>",
            "Re: API key", date(2026, 6, 11), ["UNREAD", "INBOX"]),  # client, older
    # client domain but YOU replied last → nothing owed, dropped
    _thread("Carlos <carlos@newcalgon.net>", "thanks", date(2026, 6, 26),
            ["SENT", "INBOX"]),
    # client domain but a calendar RSVP subject — dropped as noise
    _thread("Andres Birlain <andres@rivus.mx>", "Aceptado: TMN x Rivus Sync",
            date(2026, 6, 26), ["UNREAD", "INBOX"]),
    # not a client domain (vendor) — dropped by the allowlist
    _thread("Andy Braun <andy@smash.cloud>", "Re: rates", date(2026, 6, 26),
            ["UNREAD", "IMPORTANT", "INBOX"]),
    # not a client (Ampere isn't on the list) — dropped
    _thread("Craig Hardy <chardy@amperecomputing.com>",
            "FW: Invoices", date(2026, 6, 26), ["UNREAD", "IMPORTANT", "INBOX"]),
    # internal — dropped
    _thread("Abraham Noya <abraham@teamnebula.ai>", "Re: Engagement",
            date(2026, 6, 26), ["UNREAD", "INBOX"]),
    # automated — dropped
    _thread("Gemini <gemini-notes@google.com>", "Notes: Daily Standup",
            date(2026, 6, 26), ["UNREAD", "INBOX"]),
]}}


class ParseLinearTests(unittest.TestCase):
    def test_parse_issues_pulls_core_fields(self):
        issues = nebos.parse_issues(LINEAR_RESPONSE)
        self.assertEqual(len(issues), 2)
        self.assertEqual(issues[0]["id"], "TMNS-82")
        self.assertEqual(issues[0]["priority_label"], "High")
        self.assertEqual(issues[0]["due"], "2026-06-19")

    def test_overdue_high_outranks_no_priority(self):
        issues = nebos.parse_issues(LINEAR_RESPONSE)
        cands = [nebos._issue_candidate(i, TODAY) for i in issues]
        cands.sort(key=lambda c: (c["score"], c["tiebreak"], c["id2"]))
        self.assertIn("TMNS-82", cands[0]["line"])
        self.assertIn("overdue", cands[0]["line"])


class ParseGmailTests(unittest.TestCase):
    def test_only_client_domains_kept(self):
        emails = nebos.parse_client_emails(GMAIL_RESPONSE, TODAY)
        froms = {e["name"] for e in emails}
        self.assertIn("Luis Patino", froms)        # rivus.mx — client
        self.assertIn("Angelica Killingsworth", froms)  # rs21.io — client
        self.assertNotIn("Andy Braun", froms)      # smash.cloud — vendor
        self.assertNotIn("Craig Hardy", froms)     # amperecomputing.com — not listed
        self.assertNotIn("Abraham Noya", froms)    # internal
        self.assertNotIn("Gemini", froms)          # automated
        self.assertNotIn("Carlos", froms)          # client domain but SENT last
        self.assertNotIn("Andres Birlain", froms)  # client domain but calendar RSVP
        self.assertEqual(len(emails), 2)

    def test_subject_strips_repeated_prefixes(self):
        emails = nebos.parse_client_emails(GMAIL_RESPONSE, TODAY)
        luis = next(e for e in emails if e["name"] == "Luis Patino")
        self.assertEqual(luis["subject"], "Next steps for Carlos")  # "Re: Re:" gone
        self.assertEqual(luis["org"], "Rivus")
        self.assertTrue(luis["important"])

    def test_explicit_domains_override_the_default(self):
        emails = nebos.parse_client_emails(
            GMAIL_RESPONSE, TODAY, domains={"amperecomputing.com"})
        froms = {e["name"] for e in emails}
        self.assertIn("Craig Hardy", froms)        # now allowed
        self.assertNotIn("Luis Patino", froms)     # no longer on the list

    def test_recent_email_is_hotter_than_old(self):
        emails = nebos.parse_client_emails(GMAIL_RESPONSE, TODAY)
        luis = nebos._email_candidate(next(e for e in emails if e["name"] == "Luis Patino"))
        ang = nebos._email_candidate(next(e for e in emails if e["name"] == "Angelica Killingsworth"))
        self.assertLess(luis["score"], ang["score"])


class BuildSectionTests(unittest.TestCase):
    def _call(self, slug, args):
        return {"LINEAR_RUN_QUERY_OR_MUTATION": LINEAR_RESPONSE,
                "GMAIL_LIST_THREADS": GMAIL_RESPONSE}[slug]

    def test_no_gateway_configured_returns_none(self):
        self.assertIsNone(nebos.build_section(TODAY, env={}))

    def test_top5_merges_issues_and_client_emails_in_priority_order(self):
        out = nebos.build_section(TODAY, env={}, call=self._call)
        self.assertIn("🎯 Top 5 — NEBOS", out)
        # overdue High issue leads, hot client email next, no-priority chore last
        self.assertLess(out.index("TMNS-82"), out.index("Luis Patino"))
        self.assertLess(out.index("Luis Patino"), out.index("TMN-926"))
        self.assertIn("Reply to Luis Patino (Rivus)", out)
        self.assertNotIn("Andy Braun", out)        # vendor filtered out
        self.assertNotIn("Craig Hardy", out)       # not a listed client

    def test_caps_at_five_items(self):
        out = nebos.build_section(TODAY, env={}, call=self._call)
        bullets = [l for l in out.splitlines() if l.startswith("•")]
        self.assertLessEqual(len(bullets), 5)

    def test_both_sources_failing_returns_none(self):
        def boom(slug, args):
            raise OSError("composio down")
        self.assertIsNone(nebos.build_section(TODAY, env={}, call=boom))

    def test_one_source_failing_still_renders_the_other(self):
        def call(slug, args):
            if slug == "GMAIL_LIST_THREADS":
                raise OSError("gmail down")
            return LINEAR_RESPONSE
        out = nebos.build_section(TODAY, env={}, call=call)
        self.assertIn("TMNS-82", out)
        self.assertNotIn("Luis Patino", out)


class ShawnActionSectionTests(unittest.TestCase):
    def _linear_call(self, slug, args):
        self.assertEqual(slug, "LINEAR_RUN_QUERY_OR_MUTATION")
        return LINEAR_RESPONSE

    def test_combines_only_shawn_meeting_actions_and_assigned_linear(self):
        items = [
            {"mine": True, "text": "Shawn to send the revised SOW to Andres.",
             "title": "Carlos Sync", "day": "yesterday", "age_days": 1},
            {"mine": False, "text": "Maira to prepare the deck.",
             "title": "Sales Sync", "day": "today", "age_days": 0},
        ]
        out = nebos.build_shawn_action_section(
            TODAY, env={}, call=self._linear_call, meeting_items=items)
        self.assertIn("🎯 Shawn — Top 5 actions", out)
        self.assertIn("Send the revised SOW", out)
        self.assertNotIn("Shawn to send", out)
        self.assertIn("meeting note: Carlos Sync, yesterday", out)
        self.assertIn("Follow up on Lockheed Martin — Linear TMNS-82", out)
        self.assertNotIn("Maira to prepare", out)
        self.assertNotIn("Reply to Luis", out)

    def test_meeting_actions_work_when_linear_is_unconfigured(self):
        out = nebos.build_shawn_action_section(TODAY, env={}, meeting_items=[
            {"mine": True, "text": "Shawn to send pricing.",
             "title": "Pricing Call", "day": "today", "age_days": 0},
        ])
        self.assertIn("Send pricing", out)

    def test_combined_actions_are_deduped_and_capped_at_five(self):
        items = [
            {"mine": True, "text": f"Shawn to do item {i}.",
             "title": f"Call {i}", "day": "today", "age_days": 0}
            for i in range(7)
        ]
        items.append({**items[0], "title": "Duplicate Call"})
        out = nebos.build_shawn_action_section(TODAY, env={}, meeting_items=items)
        bullets = [line for line in out.splitlines() if line.startswith("• ")]
        self.assertEqual(len(bullets), 5)
        self.assertEqual(sum("Do item 0" in line for line in bullets), 1)


if __name__ == "__main__":
    unittest.main()
