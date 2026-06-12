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
import shutil
import subprocess
import urllib.request
from datetime import date
from pathlib import Path

logger = logging.getLogger(__name__)

MODEL = "claude-opus-4-8"
API_URL = "https://api.anthropic.com/v1/messages"
HERMES_BIN = os.environ.get("HERMES_BIN") or str(Path.home() / ".local" / "bin" / "hermes")
HERMES_TIMEOUT = 300
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
things look steady rather than inventing concern.

Comparison and judgment:
- You usually only have the PREVIOUS export and the CURRENT one. That is \
enough — compare the latest reading to the previous reading and call a \
direction. You do NOT need a week of history or a confirmed pattern; a \
single-day change is worth reporting.
- For Bella, you are given her breed, color, age, and life stage. Use what is \
normal for a healthy dog of that breed and age to judge a single reading, even \
with no history. For example, for an adult Labrador in her prime, weigh whether \
today's activity, eating, drinking, licking, scratching, and rest look normal, \
low, or worth watching for a dog like her. Flag anything that looks off for her \
breed and age (excess drinking, appetite drop, a scratching or licking spike \
that can mean skin or allergy trouble), but stay calm and factual — you are \
giving Shawn a heads-up, not a diagnosis. Still no numbers in the output."""


# -------------------------------------------------------------------- prompt
def _profile_line(profile: dict | None) -> str | None:
    if not profile:
        return None
    bits = [profile.get("color"), profile.get("breed")]
    desc = " ".join(b for b in bits if b)
    age = profile.get("age_years")
    stage = profile.get("life_stage")
    sex = profile.get("sex")
    wt = profile.get("weight_lbs")
    extra = []
    if sex and sex != "unknown":
        extra.append(sex)
    if age is not None:
        extra.append(f"{age} years old")
    if stage and stage != "unknown":
        extra.append(stage)
    if wt:
        extra.append(f"around {wt} lbs")
    return f"Bella is a {desc} ({', '.join(extra)})." if extra else f"Bella is a {desc}."


def build_prompt(current: dict, previous: dict | None, today: date) -> tuple[str, str]:
    parts = [f"Today is {today:%A, %B} (year and day intentionally withheld)."]
    pline = _profile_line(current.get("bella_profile"))
    if pline:
        parts += [pline + " Use what is typical for a dog like her to judge "
                  "her readings."]
    parts += ["CURRENT export:", json.dumps(current, sort_keys=True)]
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


def _call_hermes(prompt: str) -> str:
    """One-shot Hermes (codex OAuth — no API spend). Stdout is the answer."""
    r = subprocess.run([HERMES_BIN, "-z", prompt], capture_output=True,
                       text=True, timeout=HERMES_TIMEOUT)
    if r.returncode != 0:
        raise RuntimeError(f"hermes exited {r.returncode}: {r.stderr[-200:]}")
    return r.stdout.strip()


_UNSET = object()


# ------------------------------------------------------------------ generate
def generate_digest(current: dict, previous: dict | None, today: date, *,
                    hermes=_UNSET, call=None, api_key: str | None = None) -> str | None:
    """LLM-written digest body, or None when the caller should fall back.

    Backend order: Hermes (local agent, no API spend) → Anthropic API →
    None (caller renders the deterministic trends fallback). Any output
    containing a digit is rejected and the next backend is tried.
    """
    system, user = build_prompt(current, previous, today)

    def _clean(text: str | None) -> str | None:
        if not text or re.search(r"\d", text):
            return None
        return text

    if hermes is _UNSET:
        hermes = _call_hermes if shutil.which(HERMES_BIN) or Path(HERMES_BIN).exists() else None
    if hermes is not None:
        try:
            out = _clean(hermes(f"{system}\n\n{user}"))
            if out:
                return out
            logger.warning("llm: hermes output rejected (empty or contains digits)")
        except Exception as exc:
            logger.warning("llm: hermes backend failed: %s", exc)

    api_key = os.environ.get("ANTHROPIC_API_KEY", "") if api_key is None else api_key
    if not api_key:
        return None
    if call is None:
        call = lambda s, u: _call_anthropic(s, u, api_key=api_key)
    try:
        out = _clean(call(system, user))
        if out:
            return out
        logger.warning("llm: anthropic output rejected (empty or contains digits)")
    except Exception as exc:
        logger.warning("llm: anthropic backend failed: %s", exc)
    return None


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
