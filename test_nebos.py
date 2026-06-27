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


GMAIL_RESPONSE = {"data": {"threads": [
    _thread("Craig Hardy <chardy@amperecomputing.com>",
            "FW: Invoices against PO-012985", date(2026, 6, 26),
            ["UNREAD", "IMPORTANT", "INBOX"]),
    _thread("Dre Nicholas <influencer@imprintteam.com>",
            "Re: Collab with micro-learning app", date(2026, 6, 11),
            ["UNREAD", "INBOX"]),
    # internal — your own domain, must be dropped
    _thread("Abraham Noya <abraham@teamnebula.ai>", "Re: Engagement",
            date(2026, 6, 26), ["UNREAD", "INBOX"]),
    # automated — must be dropped
    _thread("Gemini <gemini-notes@google.com>", "Notes: Daily Standup",
            date(2026, 6, 26), ["UNREAD", "INBOX"]),
    # you replied last (SENT) — nothing owed, dropped
    _thread("Someone <s@client.com>", "handled", date(2026, 6, 26),
            ["SENT", "INBOX"]),
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
    def test_internal_and_automated_senders_dropped(self):
        emails = nebos.parse_client_emails(GMAIL_RESPONSE, TODAY)
        froms = {e["name"] for e in emails}
        self.assertIn("Craig Hardy", froms)
        self.assertIn("Dre Nicholas", froms)
        self.assertNotIn("Abraham Noya", froms)   # internal domain
        self.assertNotIn("Gemini", froms)         # automated
        self.assertEqual(len(emails), 2)          # SENT thread also dropped

    def test_sender_name_org_and_subject_cleanup(self):
        emails = nebos.parse_client_emails(GMAIL_RESPONSE, TODAY)
        craig = next(e for e in emails if e["name"] == "Craig Hardy")
        self.assertEqual(craig["org"], "Amperecomputing")
        self.assertNotIn("FW:", craig["subject"])  # Re:/Fw: stripped
        self.assertTrue(craig["important"])

    def test_recent_email_is_hotter_than_old(self):
        emails = nebos.parse_client_emails(GMAIL_RESPONSE, TODAY)
        craig = nebos._email_candidate(next(e for e in emails if e["name"] == "Craig Hardy"))
        dre = nebos._email_candidate(next(e for e in emails if e["name"] == "Dre Nicholas"))
        self.assertLess(craig["score"], dre["score"])


class BuildSectionTests(unittest.TestCase):
    def _call(self, slug, args):
        return {"LINEAR_RUN_QUERY_OR_MUTATION": LINEAR_RESPONSE,
                "GMAIL_LIST_THREADS": GMAIL_RESPONSE}[slug]

    def test_no_gateway_configured_returns_none(self):
        self.assertIsNone(nebos.build_section(TODAY, env={}))

    def test_top5_merges_issues_and_emails_in_priority_order(self):
        out = nebos.build_section(TODAY, env={}, call=self._call)
        self.assertIn("🎯 Top 5 — NEBOS", out)
        # overdue High issue leads, hot client email next, no-priority chore last
        self.assertLess(out.index("TMNS-82"), out.index("Craig Hardy"))
        self.assertLess(out.index("Craig Hardy"), out.index("TMN-926"))
        self.assertIn("Reply to Craig Hardy (Amperecomputing)", out)

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
        self.assertNotIn("Craig Hardy", out)


if __name__ == "__main__":
    unittest.main()
