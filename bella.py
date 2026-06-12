"""Bella's health from her Fi Series 3 collar — fetch + history layer.

Fi has no official API; this uses the same endpoints the Fi app does
(email/password login at api.tryfi.com, then GraphQL), the approach the
pytryfi project documents. Two data paths:

- Sleep: the rest-summary feed returns multi-day history directly.
- Steps: Fi only exposes the *current* daily total, so each morning run
  appends today's reading to a local JSON history file; trends become
  readable after about a week of runs.

Rendering is delegated to trends.render_pet_section — directions only,
no numbers. Every failure path degrades to an explicit one-liner; Bella's
section must never sink the digest.
"""
from __future__ import annotations

import json
import logging
import os
import urllib.parse
import urllib.request
from datetime import date
from http.cookiejar import CookieJar
from pathlib import Path

import trends

logger = logging.getLogger(__name__)

FI_API = "https://api.tryfi.com"
DEFAULT_HISTORY = Path.home() / ".daily-digest" / "bella_history.json"
HISTORY_KEEP_DAYS = 60

PETS_QUERY = """query {
  currentUser { userHouseholds { household { pets { id name } } } }
}"""
STEPS_QUERY = """query {
  pet (id: "%s") {
    dailyStepStat: currentActivitySummary(period: DAILY) {
      ... on ActivitySummary { totalSteps }
    }
  }
}"""
REST_QUERY = """query {
  pet (id: "%s") {
    restSummaryFeed(cursor: null, period: DAILY, limit: 21) {
      restSummaries {
        start
        data { ... on ConcreteRestSummaryData { sleepAmounts { type duration } } }
      }
    }
  }
}"""


# ------------------------------------------------------------------ transport
def make_gql(email: str, password: str, timeout: float = 30.0):
    """Login once, return a `gql(kind, pet_id=...)` callable bound to the session."""
    jar = CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))

    body = urllib.parse.urlencode({"email": email, "password": password}).encode()
    req = urllib.request.Request(f"{FI_API}/auth/login", data=body, method="POST")
    with opener.open(req, timeout=timeout) as r:
        login = json.load(r)
    if not login.get("sessionId") and not login.get("userId"):
        raise RuntimeError(f"fi login rejected: {login}")

    def gql(kind: str, pet_id: str = "") -> dict:
        query = {
            "pets": PETS_QUERY,
            "steps": STEPS_QUERY % pet_id,
            "rest": REST_QUERY % pet_id,
        }[kind]
        req = urllib.request.Request(
            f"{FI_API}/graphql", data=json.dumps({"query": query}).encode(),
            method="POST", headers={"Content-Type": "application/json"})
        with opener.open(req, timeout=timeout) as r:
            return json.load(r)

    return gql


# -------------------------------------------------------------------- parsing
def _walk(obj, key):
    """First value for `key` anywhere in a nested structure."""
    if isinstance(obj, dict):
        if key in obj:
            return obj[key]
        for v in obj.values():
            found = _walk(v, key)
            if found is not None:
                return found
    elif isinstance(obj, list):
        for v in obj:
            found = _walk(v, key)
            if found is not None:
                return found
    return None


def find_pet_id(resp: dict, name: str) -> str | None:
    pets = _walk(resp, "pets") or []
    for p in pets:
        if (p.get("name") or "").lower() == name.lower():
            return p.get("id")
    return None


def parse_daily_steps(resp: dict) -> float | None:
    steps = _walk(resp, "totalSteps")
    return float(steps) if steps is not None else None


def parse_rest_summaries(resp: dict) -> dict[str, float]:
    """{iso_day: minutes of rest (sleep + naps)} — Fi reports durations in seconds."""
    out: dict[str, float] = {}
    for s in _walk(resp, "restSummaries") or []:
        day = (s.get("start") or "")[:10]
        amounts = _walk(s, "sleepAmounts") or []
        if not day or not amounts:
            continue
        out[day] = sum(float(a.get("duration") or 0) for a in amounts) / 60
    return out


# -------------------------------------------------------------------- history
def load_history(path: Path) -> dict:
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return {}


def update_history(path: Path, metric: str, day: str, value: float) -> None:
    hist = load_history(path)
    hist.setdefault(metric, {})[day] = value
    series = hist[metric]
    for stale in sorted(series)[:-HISTORY_KEEP_DAYS]:
        del series[stale]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(hist, indent=0, sort_keys=True))


# ---------------------------------------------------------------- composition
def build_section(today: date | None = None, *, env: dict | None = None,
                  history_path: Path = DEFAULT_HISTORY, gql=None,
                  pet_name: str | None = None) -> str:
    today = today or date.today()
    env = os.environ if env is None else env
    pet_name = pet_name or env.get("FI_PET_NAME", "Bella")

    email, password = env.get("FI_EMAIL"), env.get("FI_PASSWORD")
    if gql is None and not (email and password):
        return f"🐕 {pet_name}\n\nFi not configured — set FI_EMAIL + FI_PASSWORD to track her."

    try:
        if gql is None:
            gql = make_gql(email, password)
        pet_id = find_pet_id(gql("pets"), pet_name)
        if not pet_id:
            return f"🐕 {pet_name}\n\nCouldn't find {pet_name} on the Fi account."
        steps_today = parse_daily_steps(gql("steps", pet_id=pet_id))
        sleep = parse_rest_summaries(gql("rest", pet_id=pet_id))
    except Exception as exc:  # Bella's section must never sink the digest
        logger.warning("bella: fi fetch failed: %s", exc)
        return f"🐕 {pet_name}\n\nFi unavailable right now — couldn't reach the collar data."

    if steps_today is not None:
        update_history(history_path, "steps", today.isoformat(), steps_today)
    series = {"steps": load_history(history_path).get("steps", {}), "sleep": sleep}
    return trends.render_pet_section(pet_name, series, today)
