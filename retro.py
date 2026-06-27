"""Call Retro — the end-of-day counterpart to the morning digest.

Two parts, one Slack post:

- **Top 5 — NEBOS**: the same action-items + client-email section the morning
  digest leads with, re-synced at end of day so it reflects the latest state.
- **Calls**: a one-block recap of every call that happened today, sourced from
  the **NEBOS meeting store** — the canonical, Fireflies-fed record of Shawn's
  meetings. Each call renders its title, time, who it was with, a condensed
  summary, and (when the notes call them out) the next steps.

NEBOS is the ultimate source of truth: Fireflies transcribes the call, NEBOS
ingests the summary, and this retro reads NEBOS rather than any one transcription
vendor directly. Reuses the morning digest's Slack delivery. Every fetch degrades
to None so the retro never crashes; a part with nothing to show is dropped.
"""
from __future__ import annotations

import json
import logging
import os
import re
import sys
import urllib.request
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import morning
import nebos

logger = logging.getLogger(__name__)

# Timezone for "today's calls" and the header date. The retro is Shawn's
# end-of-day wrap, so the window is the calendar day in this zone. Override with
# RETRO_TZ (e.g. Europe/London) wherever Shawn is based.
RETRO_TZ = ZoneInfo(os.environ.get("RETRO_TZ", "America/Los_Angeles"))
_UNSET = object()

# NEBOS MCP endpoint — the Fireflies-fed meeting store. Bearer-token auth.
NEBOS_MCP_URL = os.environ.get("NEBOS_MCP_URL", "https://teamnebula-os.web.app/api/mcp")
MEETING_LIST = "meeting_list"

MEETING_FETCH_LIMIT = 40          # how many recent meetings to scan for "today"
CALLS_MAX = 6                     # cap calls shown; the rest collapse to "+N more"
SUMMARY_MAX_CHARS = 240
NEXT_MAX_CHARS = 200

# Internal domains — attendees here don't make a meeting an external "call",
# and aren't named in the "with" line.
_INTERNAL_DOMAINS = {"teamnebula.ai", "aiadvantageagency.co"}
# Summary bullet labels that mark follow-ups / action items.
_NEXT_LABEL = re.compile(r"next steps?|follow[\s-]?up|action items?", re.IGNORECASE)


# ------------------------------------------------------------------ transport
def make_nebos_call(env: dict, timeout: float = 40.0):
    """`call(tool, arguments)` bound to the NEBOS MCP endpoint, returning the
    decoded tool payload (NEBOS wraps the real JSON in result.content[].text,
    over either a plain JSON or an SSE `data:` response)."""
    url = env.get("NEBOS_MCP_URL", NEBOS_MCP_URL)
    token = env["NEBOS_MCP_TOKEN"]

    def call(tool: str, arguments: dict):
        body = json.dumps({"jsonrpc": "2.0", "id": "1", "method": "tools/call",
                           "params": {"name": tool, "arguments": arguments}}).encode()
        req = urllib.request.Request(
            url, data=body, method="POST",
            headers={"Authorization": f"Bearer {token}",
                     "Content-Type": "application/json",
                     "Accept": "application/json, text/event-stream"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read().decode()
        return _decode_mcp(raw)

    return call


def _decode_mcp(raw: str):
    """Pull the tool result out of a JSON or SSE MCP response, then json.loads
    the text payload NEBOS tools return."""
    obj = None
    if raw.lstrip()[:1] == "{":
        obj = json.loads(raw)
    else:                                   # SSE: last `data:` line is the message
        for line in raw.splitlines():
            if line.startswith("data:"):
                try:
                    obj = json.loads(line[5:].strip())
                except json.JSONDecodeError:
                    pass
    if obj is None:
        return None
    try:
        text = obj["result"]["content"][0]["text"]
    except (KeyError, IndexError, TypeError):
        return obj
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return text


# ------------------------------------------------------------------ time
def meeting_dt(date_field) -> datetime | None:
    """NEBOS dates come two ways: a Firestore-style {_seconds} object, or an
    ISO-8601 string ('2026-06-24T18:09:00.000Z'). Normalize both to an aware
    datetime in RETRO_TZ; return None when unparseable."""
    if isinstance(date_field, dict) and "_seconds" in date_field:
        try:
            return datetime.fromtimestamp(int(date_field["_seconds"]), timezone.utc).astimezone(RETRO_TZ)
        except (TypeError, ValueError, OverflowError):
            return None
    if isinstance(date_field, str) and date_field:
        s = date_field.replace("Z", "+00:00")
        try:
            return datetime.fromisoformat(s).astimezone(RETRO_TZ)
        except ValueError:
            return None
    return None


# ------------------------------------------------------------------ fetch
def fetch_today_meetings(call, today) -> list[dict]:
    """Meetings whose date falls on `today` (RETRO_TZ), newest first."""
    meetings = call(MEETING_LIST, {"limit": MEETING_FETCH_LIMIT})
    if not isinstance(meetings, list):
        return []
    out = []
    for m in meetings:
        if not isinstance(m, dict):
            continue
        dt = meeting_dt(m.get("date"))
        if dt and dt.date() == today:
            out.append({**m, "_dt": dt})
    out.sort(key=lambda m: m["_dt"], reverse=True)
    return out


# ------------------------------------------------------------------ parse
def _clean(s: str) -> str:
    """Strip markdown emphasis and collapse whitespace into one line."""
    s = re.sub(r"[*_`]+", "", s or "")
    return " ".join(s.split())


def _truncate(s: str, n: int) -> str:
    s = _clean(s)
    return s if len(s) <= n else s[:n].rsplit(" ", 1)[0] + "…"


def summary_bullets(summary: str) -> list[str]:
    """A NEBOS summary is either prose or a markdown bullet list. Return its
    bullets (markers stripped); for prose, a single-element list."""
    if not summary:
        return []
    bullets = []
    for line in summary.splitlines():
        line = line.strip()
        if not line:
            continue
        m = re.match(r"^[-*•]\s+(.*)$", line)
        if m:
            bullets.append(m.group(1).strip())
    if bullets:
        return bullets
    return [summary.strip()]


def split_next_steps(bullets: list[str]) -> tuple[list[str], str | None]:
    """Separate a 'Next steps / Follow-up / Action items' bullet (matched on its
    bold label) from the rest. Returns (summary_bullets, next_steps_text)."""
    rest, nxt = [], None
    for b in bullets:
        label = b.split(":", 1)[0] if ":" in b else ""
        if nxt is None and _NEXT_LABEL.search(label):
            nxt = b.split(":", 1)[1].strip() if ":" in b else b
        else:
            rest.append(b)
    return rest, nxt


def call_partners(attendees) -> str:
    """A short 'with' label: the external orgs on the invite (domains outside
    the company), title-cased, deduped. Empty when the call is internal-only."""
    orgs = []
    for a in attendees or []:
        m = re.search(r"@([\w.-]+)", a or "")
        if not m:
            continue
        domain = m.group(1).lower()
        if domain in _INTERNAL_DOMAINS:
            continue
        org = domain.split(".")[0]
        label = org.upper() if len(org) <= 4 else org.capitalize()
        if label not in orgs:
            orgs.append(label)
    return ", ".join(orgs[:3])


# ------------------------------------------------------------------ records
def call_record(m: dict) -> dict:
    """Structured, deterministic facts about one call — the unit both the
    rendered Calls section and the --json data mode are built from. Owner
    attribution of next steps is deliberately left to the composing layer."""
    title = (m.get("title") or "Call").strip()
    when = m["_dt"].strftime("%-I:%M %p").lstrip("0")
    who = call_partners(m.get("attendees"))
    rest, nxt = split_next_steps(summary_bullets(m.get("summary") or ""))
    if rest:
        # join the thematic bullets into one skimmable summary line
        body = " ".join(re.sub(r"^[^:]{0,40}:\s*", "", _clean(b)) if ":" in b else _clean(b)
                        for b in rest)
        summary = _truncate(body, SUMMARY_MAX_CHARS)
    else:
        summary = ""
    return {"title": title, "time": when, "who": who,
            "label_line": f"*{title}* ({when}" + (f", {who}" if who else "") + ")",
            "summary": summary,
            "next_steps": _truncate(nxt, NEXT_MAX_CHARS) if nxt else ""}


def _section_bullets(section: str | None) -> list[str]:
    """The `• ` lines of a rendered NEBOS section (header/blank dropped)."""
    if not section:
        return []
    return [ln for ln in section.splitlines() if ln.startswith("• ")]


# ------------------------------------------------------------------ render
def render_one_call(m: dict) -> str:
    r = call_record(m)
    lines = [r["label_line"]]
    lines.append(f"• {r['summary']}" if r["summary"] else "• No summary in the notes yet.")
    if r["next_steps"]:
        lines.append(f"• Next: {r['next_steps']}")
    return "\n".join(lines)


def render_calls_section(meetings: list[dict]) -> str:
    shown = meetings[:CALLS_MAX]
    blocks = ["📞 Calls", ""]
    blocks.append("\n\n".join(render_one_call(m) for m in shown))
    extra = len(meetings) - len(shown)
    if extra > 0:
        blocks.append(f"\n_+{extra} more call{'s' if extra != 1 else ''} today._")
    return "\n".join(blocks)


# ---------------------------------------------------------------- composition
def build_calls_section(today=None, *, env: dict | None = None, call=None) -> str | None:
    """The Calls section, or None when NEBOS isn't configured/reachable or there
    were no calls today."""
    env = os.environ if env is None else env
    today = today or datetime.now(RETRO_TZ).date()
    if call is None:
        if not env.get("NEBOS_MCP_TOKEN"):
            return None
        call = make_nebos_call(env)
    try:
        meetings = fetch_today_meetings(call, today)
    except Exception as exc:  # NEBOS must never sink the retro
        logger.warning("retro: NEBOS meeting fetch failed: %s", exc)
        return None
    if not meetings:
        return None
    return render_calls_section(meetings)


def build_retro_data(today=None, *, env: dict | None = None, call=None,
                     nebos_section=_UNSET) -> dict:
    """Deterministic facts for the Call Retro, for an LLM to compose from:
    today's calls (each with summary + raw next_steps for owner attribution)
    and the Top-5 NEBOS bullet lines. No judgment, no Slack — just the data."""
    env = os.environ if env is None else env
    today = today or datetime.now(RETRO_TZ).date()

    meetings: list[dict] = []
    if call is not None or env.get("NEBOS_MCP_TOKEN"):
        nebos_call = call or make_nebos_call(env)
        try:
            meetings = fetch_today_meetings(nebos_call, today)
        except Exception as exc:  # NEBOS hiccup → no calls, never a crash
            logger.warning("retro: NEBOS meeting fetch failed: %s", exc)
            meetings = []

    if nebos_section is _UNSET:
        nebos_section = nebos.build_section(today, env=env)

    return {
        "date": today.isoformat(),
        "label": f"{today:%A, %B %-d}",
        "call_count": len(meetings),
        "top5": _section_bullets(nebos_section),
        "calls": [call_record(m) for m in meetings],
    }


def build_retro(today=None, *, nebos_section=_UNSET, calls_section=_UNSET) -> str:
    today = today or datetime.now(RETRO_TZ).date()
    if nebos_section is _UNSET:
        nebos_section = nebos.build_section(today)
    if calls_section is _UNSET:
        calls_section = build_calls_section(today)

    header = f"🌙 Call Retro — {today:%A, %B %-d, %Y}"
    blocks = [header]
    if nebos_section:
        blocks += ["", nebos_section]
    if calls_section:
        blocks += ["", calls_section]
    return "\n".join(blocks)


def main() -> int:
    today = datetime.now(RETRO_TZ).date()
    # --json: emit deterministic data for the routine's LLM to compose the
    # action-items-first message from. The routine reads this, not the rendered
    # text below (which stays as a self-contained local/fallback render).
    if "--json" in sys.argv:
        print(json.dumps(build_retro_data(today)))
        return 0
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
