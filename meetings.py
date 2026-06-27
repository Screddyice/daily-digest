"""Today's meetings — TMN Google Calendar via Composio REST + render.

Closes the digest with what's on the calendar: one line per meeting
(time + title) and a single concise context bullet — who it's with and
what it's for when the event description gives a usable purpose (video-call
boilerplate and links are stripped, never shown).

This is the one digest section where digits are allowed (meeting times);
the no-numbers rule covers the health trends, not the schedule. Stdlib
only, same Composio REST gateway the old calendar section used. Every
failure path degrades to an explicit one-liner — meetings must never
sink the digest.
"""
from __future__ import annotations

import json
import logging
import os
import re
import urllib.request
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

PT = ZoneInfo("America/Los_Angeles")
COMPOSIO_EXECUTE = "https://backend.composio.dev/api/v3/tools/execute/{slug}"
MAX_NAMES = 4
PURPOSE_MAX_CHARS = 110
# Description lines that are call logistics or scheduler (Calendly-style)
# field labels, not meeting purpose.
_BOILERPLATE = re.compile(
    r"zoom\.us|meet\.google|teams\.microsoft|webex\.com|https?://"
    r"|zoom meeting|google meet|microsoft teams|^join\b"
    r"|dial[- ]?in|passcode|pin\b|meeting id|joining info|^[-_=~]{3,}$"
    r"|^event name$|^location$|^date & time$|^invitee|^time zone$"
    r"|^questions?$|^description$|^need to make changes"
    r"|^\d+\s*(minute|min|hour|hr)\b",
    re.IGNORECASE)


# ----------------------------------------------------------------- transport
def make_call(env: dict, timeout: float = 40.0):
    """`call(slug, arguments)` bound to the TMN Composio account."""
    key = env["NEB_COMPOSIO_MCP_API_KEY"]
    uid = env.get("NEB_COMPOSIO_MCP_USER_ID", "user_uwgmr")

    def call(slug: str, arguments: dict) -> dict:
        body = json.dumps({"user_id": uid, "arguments": arguments}).encode()
        req = urllib.request.Request(
            COMPOSIO_EXECUTE.format(slug=slug), data=body, method="POST",
            headers={"x-api-key": key, "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.load(r)

    return call


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


# -------------------------------------------------------------------- parsing
def _name_from_email(email: str) -> str:
    local = email.split("@")[0]
    return " ".join(p.capitalize() for p in re.split(r"[._-]+", local) if p)


def _guests(ev: dict) -> list[str]:
    names = []
    for a in ev.get("attendees", []):
        if a.get("self") or a.get("resource"):
            continue
        names.append(a.get("displayName") or _name_from_email(a.get("email", "")))
    return [n for n in names if n]


def _purpose(description: str | None) -> str | None:
    if not description:
        return None
    text = re.sub(r"<br\s*/?>", "\n", description)
    text = re.sub(r"<[^>]+>", " ", text)
    for line in text.splitlines():
        line = " ".join(line.split())
        if not line or _BOILERPLATE.search(line):
            continue
        if len(line) > PURPOSE_MAX_CHARS:
            line = line[:PURPOSE_MAX_CHARS].rsplit(" ", 1)[0] + "…"
        return line
    return None


def _when(ev: dict) -> str:
    start = ev.get("start", {})
    if "dateTime" in start:
        return datetime.fromisoformat(start["dateTime"]).astimezone(PT).strftime("%-I:%M %p")
    return "All day"


def _self_declined(ev: dict) -> bool:
    return any(a.get("self") and a.get("responseStatus") == "declined"
               for a in ev.get("attendees", []))


def parse_event(ev: dict) -> dict:
    return {
        "when": _when(ev),
        "title": ev.get("summary") or "(no title)",
        "who": _guests(ev),
        "purpose": _purpose(ev.get("description")),
    }


# ------------------------------------------------------------------ rendering
def _context_line(who: list[str], purpose: str | None) -> str:
    """One bullet of context per meeting: who it's with and what it's for."""
    if who:
        shown = who[:MAX_NAMES]
        if len(who) > MAX_NAMES:
            shown.append(f"and {len(who) - MAX_NAMES} more")
        whostr = "With " + ", ".join(shown)
    else:
        whostr = "Just you"
    return f"  • {whostr} — {purpose}" if purpose else f"  • {whostr}"


def render_section(parsed: list[dict]) -> str:
    L = ["📅 Meetings", ""]
    if not parsed:
        L.append("Nothing scheduled — clear day.")
        return "\n".join(L)
    for m in parsed:
        L.append(f"{m['when']} — {m['title']}")
        L.append(_context_line(m["who"], m["purpose"]))
    return "\n".join(L)


# ---------------------------------------------------------------- composition
def build_section(today, *, env: dict | None = None, call=None) -> str:
    """Today's meetings section, '' when the calendar isn't configured."""
    env = os.environ if env is None else env
    if call is None:
        if not env.get("NEB_COMPOSIO_MCP_API_KEY"):
            return ""
        call = make_call(env)
    tmin = datetime(today.year, today.month, today.day, tzinfo=PT)
    try:
        resp = call("GOOGLECALENDAR_EVENTS_LIST", {
            "calendarId": "primary",
            "timeMin": tmin.isoformat(),
            "timeMax": (tmin + timedelta(days=1)).isoformat(),
            "singleEvents": True,
            "orderBy": "startTime",
            "timeZone": "America/Los_Angeles",
            "maxResults": 25,
        })
        items = _find_items(resp) or []
    except Exception as exc:  # meetings must never sink the digest
        logger.warning("meetings: calendar fetch failed: %s", exc)
        return "📅 Meetings\n\nCalendar unavailable right now — couldn't fetch today's events."
    items = [e for e in items
             if e.get("status") != "cancelled" and not _self_declined(e)]
    return render_section([parse_event(e) for e in items])
