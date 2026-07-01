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
from datetime import datetime, timedelta, timezone

import morning
import nebos
import tzsafe

logger = logging.getLogger(__name__)

# Timezone for "today's calls" and the header date. The retro is Shawn's
# end-of-day wrap, so the window is the calendar day in this zone. Override with
# RETRO_TZ (e.g. Europe/London) wherever Shawn is based.
RETRO_TZ = tzsafe.resolve(os.environ.get("RETRO_TZ", "America/Los_Angeles"))
_UNSET = object()


def _today():
    """Today in RETRO_TZ, or a fixed RETRO_DATE (YYYY-MM-DD) for test/backfill runs."""
    override = os.environ.get("RETRO_DATE", "").strip()
    if override:
        try:
            return datetime.strptime(override, "%Y-%m-%d").date()
        except ValueError:
            logger.warning("retro: ignoring bad RETRO_DATE=%r", override)
    return datetime.now(RETRO_TZ).date()

# NEBOS MCP endpoint — the Fireflies-fed meeting store. Bearer-token auth.
NEBOS_MCP_URL = os.environ.get("NEBOS_MCP_URL", "https://teamnebula-os.web.app/api/mcp")
MEETING_LIST = "meeting_list"

MEETING_FETCH_LIMIT = 40          # how many recent meetings to scan for "today"
CALLS_MAX = 6                     # cap calls shown; the rest collapse to "+N more"
SUMMARY_MAX_CHARS = 150           # one tight clause per call — the retro leads with actions, not recaps
NEXT_MAX_CHARS = 220

# Morning "Still pending" section — open to-dos carried forward from the last few
# days of calls (the retro shows them end-of-day; the morning digest reminds you
# they're still open). Shawn-owned items lead.
PENDING_LOOKBACK_DAYS = int(os.environ.get("PENDING_LOOKBACK_DAYS", "3"))
PENDING_MAX_ITEMS = 8
PENDING_ITEM_MAX_CHARS = 140      # tighter than the retro's Next: line — this is a reminder
_SHAWN = re.compile(r"\bshawn\b", re.IGNORECASE)

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
    return fetch_meetings_in_window(call, today, today)


def fetch_meetings_in_window(call, lo_date, hi_date) -> list[dict]:
    """Meetings whose date falls in [lo_date, hi_date] (RETRO_TZ), newest first."""
    meetings = call(MEETING_LIST, {"limit": MEETING_FETCH_LIMIT})
    if not isinstance(meetings, list):
        return []
    out = []
    for m in meetings:
        if not isinstance(m, dict):
            continue
        dt = meeting_dt(m.get("date"))
        if dt and lo_date <= dt.date() <= hi_date:
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
        # the headline point only — one tight clause, label stripped. The retro
        # is about what's still pending, not a full recap of each call.
        head = rest[0]
        head = re.sub(r"^[^:]{0,40}:\s*", "", _clean(head)) if ":" in head else _clean(head)
        summary = _truncate(head, SUMMARY_MAX_CHARS)
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
    today = today or _today()
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


# ----------------------------------------------------- morning "Still pending"
def _day_label(d, today) -> str:
    """Relative day for a pending item: today / yesterday / a weekday name."""
    delta = (today - d).days
    if delta <= 0:
        return "today"
    if delta == 1:
        return "yesterday"
    return d.strftime("%A")


def pending_items(meetings: list[dict], today) -> list[dict]:
    """Open to-dos from recent calls' next steps, one per call that has them.
    Each: {mine, text, title, day}. `mine` (mentions Shawn) is the lead signal —
    it's his digest, so his open items come first."""
    items = []
    for m in meetings:
        r = call_record(m)
        nxt = r["next_steps"]
        if not nxt:
            continue
        items.append({
            "mine": bool(_SHAWN.search(nxt)),
            "text": _truncate(nxt, PENDING_ITEM_MAX_CHARS),
            "title": r["title"],
            "day": _day_label(m["_dt"].date(), today),
        })
    # Shawn's items first; otherwise preserve newest-first order (stable sort).
    items.sort(key=lambda it: not it["mine"])
    return items


def render_pending_section(items: list[dict]) -> str:
    shown = items[:PENDING_MAX_ITEMS]
    L = ["📋 Still pending", ""]
    for it in shown:
        L.append(f"• {it['text']} (from {it['title']}, {it['day']})")
    extra = len(items) - len(shown)
    if extra > 0:
        L.append(f"_+{extra} more open item{'s' if extra != 1 else ''}._")
    return "\n".join(L)


def build_pending_section(today=None, *, env: dict | None = None, call=None,
                          days: int | None = None) -> str | None:
    """Open action items carried forward from the last `days` of calls (default
    PENDING_LOOKBACK_DAYS), Shawn's first. None when NEBOS isn't configured/
    reachable or nothing is pending — so the morning digest drops the section."""
    env = os.environ if env is None else env
    today = today or _today()
    days = PENDING_LOOKBACK_DAYS if days is None else days
    if call is None:
        if not env.get("NEBOS_MCP_TOKEN"):
            return None
        call = make_nebos_call(env)
    try:
        meetings = fetch_meetings_in_window(call, today - timedelta(days=days - 1), today)
    except Exception as exc:  # NEBOS must never sink the digest
        logger.warning("pending: NEBOS meeting fetch failed: %s", exc)
        return None
    items = pending_items(meetings, today)
    if not items:
        return None
    return render_pending_section(items)


def build_retro_data(today=None, *, env: dict | None = None, call=None,
                     nebos_section=_UNSET) -> dict:
    """Deterministic facts for the Call Retro, for an LLM to compose from:
    today's calls (each with summary + raw next_steps for owner attribution)
    and the Top-5 NEBOS bullet lines. No judgment, no Slack — just the data.

    Never raises: any source that errors degrades to empty and flips the
    ``degraded`` flag, so the routine can retry (or say so honestly) rather than
    silently posting a misleading empty retro on a transient blip.
    """
    env = os.environ if env is None else env
    today = today or _today()
    degraded = False

    meetings: list[dict] = []
    if call is not None or env.get("NEBOS_MCP_TOKEN"):
        nebos_call = call or make_nebos_call(env)
        try:
            meetings = fetch_today_meetings(nebos_call, today)
        except Exception as exc:  # NEBOS hiccup → no calls, never a crash
            logger.warning("retro: NEBOS meeting fetch failed: %s", exc)
            degraded = True

    if nebos_section is _UNSET:
        try:
            nebos_section = nebos.build_section(today, env=env)
        except Exception as exc:  # Top-5 source hiccup → drop it, never a crash
            logger.warning("retro: NEBOS Top-5 build failed: %s", exc)
            nebos_section = None
            degraded = True

    calls = []
    for m in meetings:
        try:
            calls.append(call_record(m))
        except Exception as exc:  # one malformed meeting shouldn't sink the batch
            logger.warning("retro: skipping unrenderable meeting: %s", exc)
            degraded = True

    return {
        "date": today.isoformat(),
        "label": f"{today:%A, %B %-d}",
        "call_count": len(calls),
        "top5": _section_bullets(nebos_section),
        "calls": calls,
        "degraded": degraded,
    }


def build_retro(today=None, *, nebos_section=_UNSET, calls_section=_UNSET) -> str:
    today = today or _today()
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
    today = _today()
    # --json: emit deterministic data for the routine's LLM to compose the
    # action-items-first message from. The routine reads this, not the rendered
    # text below (which stays as a self-contained local/fallback render).
    if "--json" in sys.argv:
        # The routine consumes this JSON, so it must ALWAYS be valid — even if
        # every source is down. build_retro_data already degrades gracefully;
        # this last-resort guard means an unforeseen error still yields a
        # parseable (degraded) payload instead of empty stdout, so the routine
        # retries rather than DMing "data unavailable" on a transient blip.
        try:
            data = build_retro_data(today)
        except Exception as exc:
            logger.warning("retro: build_retro_data crashed, emitting empty payload: %s", exc)
            data = {"date": today.isoformat(), "label": f"{today:%A, %B %-d}",
                    "call_count": 0, "top5": [], "calls": [], "degraded": True}
        print(json.dumps(data))
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
