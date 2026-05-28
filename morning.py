#!/usr/bin/env python3
"""Personal morning digest — health (live HAE), today's meetings (NEB Google
Calendar via Composio), and priorities.

Stdlib only. Intended to run on neb-server, where HAE is reachable locally and
the NEB Composio key is in the environment.
"""
from __future__ import annotations

import json
import os
import urllib.request
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import health

PT = ZoneInfo("America/Los_Angeles")
COMPOSIO_EXECUTE = "https://backend.composio.dev/api/v3/tools/execute/{slug}"


def _composio(slug: str, arguments: dict, timeout: float = 40.0) -> dict:
    """Execute one Composio tool against the NEB account via the REST gateway."""
    key = os.environ["NEB_COMPOSIO_MCP_API_KEY"]
    uid = os.environ.get("NEB_COMPOSIO_MCP_USER_ID", "user_uwgmr")
    body = json.dumps({"user_id": uid, "arguments": arguments}).encode()
    req = urllib.request.Request(
        COMPOSIO_EXECUTE.format(slug=slug), data=body, method="POST",
        headers={"x-api-key": key, "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def _find_items(obj):
    """Composio nests the Google payload at varying depths; find the events list."""
    if isinstance(obj, dict):
        if isinstance(obj.get("items"), list):
            return obj["items"]
        for v in obj.values():
            found = _find_items(v)
            if found is not None:
                return found
    elif isinstance(obj, list):
        for v in obj:
            found = _find_items(v)
            if found is not None:
                return found
    return None


def meetings_section(today) -> str:
    tmin = datetime(today.year, today.month, today.day, tzinfo=PT)
    tmax = tmin + timedelta(days=1)
    try:
        resp = _composio("GOOGLECALENDAR_EVENTS_LIST", {
            "calendarId": "primary",
            "timeMin": tmin.isoformat(),
            "timeMax": tmax.isoformat(),
            "singleEvents": True,
            "orderBy": "startTime",
            "timeZone": "America/Los_Angeles",
            "maxResults": 25,
        })
        items = _find_items(resp) or []
    except Exception as exc:
        return f"\U0001f4c5 Meetings\n  (calendar unavailable: {exc})"

    items = [e for e in items if e.get("status") != "cancelled"]
    if not items:
        return "\U0001f4c5 Meetings\n  Nothing scheduled — clear day for deep work."

    lines = ["\U0001f4c5 Meetings"]
    for ev in items:
        s = ev.get("start", {})
        if "dateTime" in s:
            when = datetime.fromisoformat(s["dateTime"]).astimezone(PT).strftime("%-I:%M %p")
        else:
            when = "all day"
        guests = [a for a in ev.get("attendees", []) if not a.get("self")]
        who = f"  · {len(guests)} guests" if guests else ""
        link = ev.get("hangoutLink") or ""
        lines.append(f"  {when} — {ev.get('summary', '(no title)')}{who}" + (f"  {link}" if link else ""))
    return "\n".join(lines)


def priorities_section(today) -> str:
    """Top priorities for the day.

    TODO: wire from a real source — NEB Linear (issues assigned to Shawn,
    urgent/high, not done) or the Jarvis brain. Returns "" until then so the
    digest simply omits the section rather than showing a stub.
    """
    return ""


def build_digest(today=None) -> str:
    today = today or datetime.now(PT).date()
    blocks = [f"☀️  Morning Digest — {today:%A, %B %-d, %Y}", "", health.build_section(today)]
    meetings = meetings_section(today)
    if meetings:
        blocks += ["", meetings]
    priorities = priorities_section(today)
    if priorities:
        blocks += ["", priorities]
    return "\n".join(blocks)


def _send_slack(text: str) -> None:
    """Post to Slack if SLACK_BOT_TOKEN + SLACK_CHANNEL are configured."""
    body = json.dumps({"channel": os.environ["SLACK_CHANNEL"],
                       "text": text, "mrkdwn": True}).encode()
    req = urllib.request.Request(
        "https://slack.com/api/chat.postMessage", data=body, method="POST",
        headers={"Authorization": f"Bearer {os.environ['SLACK_BOT_TOKEN']}",
                 "Content-Type": "application/json"})
    r = json.load(urllib.request.urlopen(req, timeout=20))
    if not r.get("ok"):
        raise SystemExit(f"slack post failed: {r.get('error')}")


def main() -> int:
    text = build_digest()
    if not os.environ.get("DRY_RUN") and os.environ.get("SLACK_BOT_TOKEN") and os.environ.get("SLACK_CHANNEL"):
        _send_slack(text)
        print("morning digest posted to Slack.")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
