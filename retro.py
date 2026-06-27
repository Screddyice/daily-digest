"""Call Retro — the end-of-day counterpart to the morning digest.

Two parts, one Slack post:

- **Top 5 — NEBOS**: the same action-items + client-email section the morning
  digest leads with, re-synced at end of day so it reflects the latest state.
- **Call**: one line summarizing the day's call and one line of its action
  items, parsed from the meeting's Gemini notes (the auto-generated docs each
  carry a `Summary` and a `Next steps` section).

"The call" is the most recent "Notes by Gemini" doc; set NEBOS_RETRO_MEETING to
pin it to a specific recurring meeting (e.g. "Call Retro") once those notes
exist. Reuses the morning digest's Composio gateway and Slack delivery. Every
fetch degrades to None so the retro never crashes; a part with nothing to show
is simply dropped.
"""
from __future__ import annotations

import logging
import os
import re
from datetime import datetime
from zoneinfo import ZoneInfo

import morning
import nebos

logger = logging.getLogger(__name__)

PT = ZoneInfo("America/Los_Angeles")
_UNSET = object()

NOTES_SUFFIX = "Notes by Gemini"          # Gemini names every notes doc "… - Notes by Gemini"
RETRO_MEETING = os.environ.get("NEBOS_RETRO_MEETING", "")  # optional title filter
SUMMARY_MAX_CHARS = 220
ACTIONS_MAX_ITEMS = 6
ACTIONS_MAX_CHARS = 320

FIND_SLUG = "GOOGLEDRIVE_FIND_FILE"
DOC_SLUG = "GOOGLEDOCS_GET_DOCUMENT_PLAINTEXT"

# Section headers Gemini emits in its notes docs — used as block boundaries.
_HEADERS = {"summary", "decisions", "needs further discussion", "aligned",
            "next steps", "action items", "details", "transcript"}


# ------------------------------------------------------------------ fetch
def find_latest_notes(call, *, meeting: str = "") -> tuple[str | None, str | None]:
    """(doc_id, name) of the newest Gemini notes doc, or (None, None)."""
    q = f"name contains '{NOTES_SUFFIX}' and trashed = false"
    if meeting:
        q = f"name contains '{meeting}' and " + q
    resp = call(FIND_SLUG, {"q": q, "orderBy": "modifiedTime desc",
                            "fields": "files(id,name,modifiedTime)", "pageSize": 5})
    files = nebos._walk(resp, "files") or []
    if not files:
        return None, None
    return files[0].get("id"), files[0].get("name") or ""


def fetch_notes_text(call, doc_id: str) -> str:
    return nebos._walk(call(DOC_SLUG, {"document_id": doc_id}), "plain_text") or ""


# ------------------------------------------------------------------ parse
def _truncate(s: str, n: int) -> str:
    s = " ".join(s.split())
    return s if len(s) <= n else s[:n].rsplit(" ", 1)[0] + "…"


def parse_summary(text: str) -> str | None:
    """The first substantive line under the notes' `Summary` header."""
    lines = [l.strip() for l in text.splitlines()]
    for i, line in enumerate(lines):
        if line.lower() == "summary":
            for nxt in lines[i + 1:]:
                if not nxt:
                    continue
                if nxt.lower() in _HEADERS:   # empty summary, hit next section
                    return None
                return _truncate(nxt, SUMMARY_MAX_CHARS)
    return None


def _owner_short(owner: str) -> str:
    o = owner.strip()
    if o.lower() in {"the group", "the team", "everyone", "all", "group", "team"}:
        return "Team"
    return o.split()[0] if o else "—"


def _task_short(task: str) -> str:
    """First clause of an action item: 'Send Options: do X.' -> 'Send Options'."""
    return re.split(r"[:.]", task.strip(), 1)[0].strip()


def parse_action_items(text: str) -> list[tuple[str, str]]:
    """[(owner, task)] from the notes' `Next steps` block."""
    lines = [l.strip() for l in text.splitlines()]
    out, in_block = [], False
    for line in lines:
        low = line.lower()
        if low in ("next steps", "action items"):
            in_block = True
            continue
        if in_block:
            if low in _HEADERS:               # reached the next section
                break
            m = re.match(r"^[-*•]\s*\[(.+?)\]\s*(.+)$", line)
            if m:
                out.append((_owner_short(m.group(1)), _task_short(m.group(2))))
    return out


# ------------------------------------------------------------------ render
def _meeting_label(doc_name: str) -> str:
    """'Daily Standup - 2026/06/26 … - Notes by Gemini' -> 'Daily Standup'."""
    return (doc_name.split(" - ")[0].strip() if doc_name else "Call") or "Call"


def render_call_section(doc_name: str, summary: str | None,
                        items: list[tuple[str, str]]) -> str:
    L = [f"📞 {_meeting_label(doc_name)}", ""]
    L.append(f"• Summary: {summary or 'no summary in the notes.'}")
    if items:
        shown = items[:ACTIONS_MAX_ITEMS]
        line = "; ".join(f"{o}: {t}" for o, t in shown)
        extra = len(items) - len(shown)
        if extra > 0:
            line += f"; +{extra} more"
        line = _truncate(line, ACTIONS_MAX_CHARS)
    else:
        line = "none noted."
    L.append(f"• Action items: {line}")
    return "\n".join(L)


# ---------------------------------------------------------------- composition
def build_call_section(today=None, *, env: dict | None = None, call=None) -> str | None:
    """The Call section, or None when there are no recent notes to summarize."""
    env = os.environ if env is None else env
    if call is None:
        if not env.get("NEB_COMPOSIO_MCP_API_KEY"):
            return None
        call = nebos.make_call(env)
    try:
        doc_id, name = find_latest_notes(call, meeting=env.get("NEBOS_RETRO_MEETING", RETRO_MEETING))
        if not doc_id:
            return None
        text = fetch_notes_text(call, doc_id)
    except Exception as exc:  # notes must never sink the retro
        logger.warning("retro: notes fetch failed: %s", exc)
        return None
    summary = parse_summary(text)
    items = parse_action_items(text)
    if not summary and not items:
        return None
    return render_call_section(name, summary, items)


def build_retro(today=None, *, nebos_section=_UNSET, call_section=_UNSET) -> str:
    today = today or datetime.now(PT).date()
    if nebos_section is _UNSET:
        nebos_section = nebos.build_section(today)
    if call_section is _UNSET:
        call_section = build_call_section(today)

    header = f"🌙 Call Retro — {today:%A, %B %-d, %Y}"
    blocks = [header]
    if nebos_section:
        blocks += ["", nebos_section]
    if call_section:
        blocks += ["", call_section]
    return "\n".join(blocks)


def main() -> int:
    today = datetime.now(PT).date()
    text = build_retro(today)
    if not os.environ.get("DRY_RUN") and os.environ.get("SLACK_BOT_TOKEN") \
            and os.environ.get("SLACK_CHANNEL"):
        morning._send_slack(text)
        print("call retro posted to Slack.")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
