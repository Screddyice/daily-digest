"""LLM digest writer — compares this export to the previous one and writes
the conclusions in plain English, no numbers.

Per Shawn's spec: no statistical baseline windows. Each run snapshots the
fetched data; the next run hands Claude the previous snapshot + the current
one and lets the model judge direction and what it hints at (stress, sleep
debt, getting sick) for both Shawn and Bella.

Raw HTTP to the Anthropic Messages API (this project is deliberately
zero-dependency for systemd deploy). A digit-gate rejects any output
containing numerals; the caller then falls back to the deterministic
trends renderer, so a numberless digest is guaranteed either way.
"""
from __future__ import annotations

import json
import logging
import os
import re
import urllib.request
from datetime import date
from pathlib import Path

logger = logging.getLogger(__name__)

MODEL = "claude-opus-4-8"
API_URL = "https://api.anthropic.com/v1/messages"
SNAPSHOT_DIR = Path.home() / ".daily-digest" / "snapshots"
SNAPSHOT_KEEP = 14

SYSTEM = """You write Shawn's private morning health digest. You are given two \
data exports: the previous one and the current one (Apple Health metrics for \
Shawn, Fi collar data for his dog Bella). Compare them and report ONLY trends \
and what they hint at.

Hard rules:
- NEVER include any number, digit, percentage, or unit. Not even counts like \
"3 days" — say "a few days". The digest must contain zero numerals.
- Every line states a direction (increasing / decreasing / steady) or a \
plain-English conclusion, never a value.
- Two sections, exactly this shape: a "💪 You" section with one short bullet \
each for: activity & exercise, lungs (blood oxygen), stress signals (recovery \
markers like HRV and resting heart rate), sleep (enough or not), and an \
illness watch (combinations hinting he's getting sick). Then a "🐕 Bella" \
section with bullets for her activity and rest.
- Bullets start with "• ". No markdown headers, no bold, no tables.
- If the current export's data is stale or missing for a stretch, open the \
affected section with a "⚠️" line saying the data hasn't synced and since \
roughly when (in words, never a date with digits).
- Be direct and human. Say what matters and stop. If nothing is notable, say \
things look steady rather than inventing concern."""


# -------------------------------------------------------------------- prompt
def build_prompt(current: dict, previous: dict | None, today: date) -> tuple[str, str]:
    parts = [f"Today is {today:%A, %B} (year and day intentionally withheld).",
             "CURRENT export:", json.dumps(current, sort_keys=True)]
    if previous:
        parts += ["PREVIOUS export:", json.dumps(previous, sort_keys=True)]
    else:
        parts.append("There is no previous export yet — describe today's data "
                     "qualitatively and note trends will start tomorrow.")
    parts.append("Write the digest body now (the two sections only, no date header).")
    return SYSTEM, "\n\n".join(parts)


# ----------------------------------------------------------------- transport
def _call_anthropic(system: str, user: str, *, api_key: str, timeout: float = 120.0) -> str:
    body = json.dumps({
        "model": MODEL,
        "max_tokens": 2000,
        "system": system,
        "messages": [{"role": "user", "content": user}],
    }).encode()
    req = urllib.request.Request(API_URL, data=body, method="POST", headers={
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01",
        "Content-Type": "application/json",
    })
    with urllib.request.urlopen(req, timeout=timeout) as r:
        resp = json.load(r)
    if resp.get("stop_reason") == "refusal":
        raise RuntimeError("model refused")
    return "".join(b.get("text", "") for b in resp.get("content", [])
                   if b.get("type") == "text").strip()


# ------------------------------------------------------------------ generate
def generate_digest(current: dict, previous: dict | None, today: date, *,
                    call=None, api_key: str | None = None) -> str | None:
    """LLM-written digest body, or None when the caller should fall back."""
    api_key = os.environ.get("ANTHROPIC_API_KEY", "") if api_key is None else api_key
    if not api_key:
        return None
    if call is None:
        call = lambda s, u: _call_anthropic(s, u, api_key=api_key)
    try:
        system, user = build_prompt(current, previous, today)
        text = call(system, user)
    except Exception as exc:
        logger.warning("llm: digest generation failed: %s", exc)
        return None
    if not text or re.search(r"\d", text):  # numbers are banned — fall back
        logger.warning("llm: output rejected (empty or contains digits)")
        return None
    return text


# ----------------------------------------------------------------- snapshots
def save_snapshot(snap_dir: Path, today: date, you: dict, bella: dict) -> None:
    snap_dir.mkdir(parents=True, exist_ok=True)
    path = snap_dir / f"{today.isoformat()}.json"
    path.write_text(json.dumps({"date": today.isoformat(), "you": you, "bella": bella},
                               sort_keys=True))
    for stale in sorted(snap_dir.glob("*.json"))[:-SNAPSHOT_KEEP]:
        stale.unlink()


def load_previous_snapshot(snap_dir: Path, today: date) -> dict | None:
    """Most recent snapshot strictly before today (same-day reruns skip their own)."""
    candidates = sorted(p for p in snap_dir.glob("*.json")
                        if p.stem < today.isoformat())
    if not candidates:
        return None
    try:
        return json.loads(candidates[-1].read_text())
    except (OSError, ValueError):
        return None
