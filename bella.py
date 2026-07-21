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
import re
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone
from http.cookiejar import CookieJar
from pathlib import Path

import trends

logger = logging.getLogger(__name__)

FI_API = "https://api.tryfi.com"
DEFAULT_HISTORY = Path.home() / ".daily-digest" / "bella_history.json"
DEFAULT_PROFILE = Path.home() / ".daily-digest" / "bella_profile.json"
HISTORY_KEEP_DAYS = 60
MAX_REST_MINUTES_PER_DAY = 24 * 60
# Steps and behavior-event counts accumulate one reading per run, so they need a
# store that survives between runs. On a durable host that's the local file
# above; in an ephemeral cloud sandbox the filesystem is wiped each run, so set
# BELLA_HISTORY_GIST (a secret gist id) + GITHUB_TOKEN and the history round-trips
# through the gist instead. Sleep is unaffected (Fi serves its history live).
GIST_FILE = "bella_history.json"
# Fi doesn't expose coat color; Shawn's Bella is a chocolate Lab.
DEFAULT_COLOR = os.environ.get("FI_PET_COLOR", "chocolate")

PETS_QUERY = """query {
  currentUser { userHouseholds { household { pets { id name } } } }
}"""
PROFILE_QUERY = """query {
  pet (id: "%s") {
    name gender weight yearOfBirth monthOfBirth dayOfBirth
    breed { name }
  }
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

# Series 3+ AI behavior trends (barking/eating/drinking/licking/scratching +
# rest/activity). Captured from the Fi app; same /graphql endpoint + session.
TRENDS_QUERY = """query HealthTrends($petId: ID!, $period: PetHealthTrendPeriod!) {
  getPetHealthTrendsForPet(petId: $petId, period: $period) {
    period
    genericTrends { ...T }
    behaviorTrends { ...T }
  }
}
fragment T on PetHealthTrend {
  title disabled
  summaryComponents {
    eventsSummary durationSummary
    eventsChange { direction change }
    durationChange { direction change }
  }
}"""

# (trend title) -> (events history key, duration history key). Steps & sleep keep
# their own keys (from the step/rest feeds) so we don't double-store them.
TREND_KEYS = {
    "Activity":   ("activity_steps", None),
    "Rest":       (None, "rest_min"),
    "Barking":    ("barking_events", None),
    "Eating":     ("eating_events", "eating_min"),
    "Drinking":   ("drinking_events", None),
    "Licking":    ("licking_events", "licking_min"),
    "Scratching": ("scratching_events", None),
}


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
        if kind == "trends":
            payload = {"query": TRENDS_QUERY,
                       "variables": {"petId": pet_id, "period": "DAY"}}
        else:
            query = {
                "pets": PETS_QUERY,
                "profile": PROFILE_QUERY % pet_id,
                "steps": STEPS_QUERY % pet_id,
                "rest": REST_QUERY % pet_id,
            }[kind]
            payload = {"query": query}
        req = urllib.request.Request(
            f"{FI_API}/graphql", data=json.dumps(payload).encode(),
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


def _life_stage(breed: str, age_years: float) -> str:
    """Large-breed life stage (Labs hit senior earlier than small dogs)."""
    if age_years < 1:
        return "puppy"
    if age_years < 2:
        return "young adult"
    if age_years < 7:
        return "adult (prime years)"
    return "senior"


def parse_profile(resp: dict, today: date, *, color: str | None = None) -> dict:
    """Breed/age/weight context used to judge a single reading against what's
    normal for this dog — even before there's any trend history."""
    p = _walk(resp, "pet") if "pet" not in resp else resp["pet"]
    p = p or (resp.get("data", {}) or {}).get("pet", {}) or {}
    y, m, d = p.get("yearOfBirth"), p.get("monthOfBirth") or 1, p.get("dayOfBirth") or 1
    age_years = None
    if y:
        born = date(y, m, d)
        age_years = (today - born).days // 365
    kg = p.get("weight")
    breed = (p.get("breed") or {}).get("name", "dog")
    return {
        "name": p.get("name", "Bella"),
        "breed": breed,
        "color": color or DEFAULT_COLOR,
        "sex": (p.get("gender") or "").lower() or "unknown",
        "age_years": age_years,
        "weight_lbs": round(kg * 2.20462) if kg else None,
        "life_stage": _life_stage(breed, age_years) if age_years is not None else "unknown",
    }


def save_profile(path: Path, profile: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(profile, sort_keys=True))


def load_profile(path: Path = DEFAULT_PROFILE) -> dict | None:
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return None


def parse_daily_steps(resp: dict) -> float | None:
    steps = _walk(resp, "totalSteps")
    return float(steps) if steps is not None else None


def parse_count(text: str | None) -> float | None:
    """'3 events' / '325 steps' / '2,987 steps/day' / '0 interruptions' -> number."""
    m = re.search(r"[\d,]+(?:\.\d+)?", text or "")
    return float(m.group().replace(",", "")) if m else None


def parse_minutes(text: str | None) -> float | None:
    """'6hr 59min' / '1min' / '5m/day' / '<1m/day' -> minutes."""
    if not text:
        return None
    if text.strip().startswith("<1"):
        return 0.5
    h = re.search(r"(\d+)\s*hr", text)
    mn = re.search(r"(\d+)\s*m(?:in)?\b", text)
    if h or mn:
        return float((int(h.group(1)) * 60 if h else 0) + (int(mn.group(1)) if mn else 0))
    return None


def parse_health_trends(resp: dict) -> dict[str, float]:
    """{history_key: today's value} for every enabled trend.

    Fi uses null summaries for unavailable/incomplete readings, so only parsed
    values are recorded. An explicit ``0 events`` remains a real zero.
    """
    t = _walk(resp, "getPetHealthTrendsForPet") or {}
    out: dict[str, float] = {}
    for grp in ("genericTrends", "behaviorTrends"):
        for tr in t.get(grp) or []:
            if tr.get("disabled"):
                continue
            keys = TREND_KEYS.get(tr.get("title"))
            if not keys:
                continue
            sc = tr.get("summaryComponents") or {}
            ev_key, dur_key = keys
            if ev_key is not None:
                value = parse_count(sc.get("eventsSummary"))
                if value is not None:
                    out[ev_key] = value
            if dur_key is not None:
                value = parse_minutes(sc.get("durationSummary"))
                if value is not None:
                    out[dur_key] = value
    return out


def parse_health_trend_directions(resp: dict) -> dict[str, str]:
    """Fi-native behavior directions keyed like the stored event series.

    Event change is authoritative. Some behaviors (notably licking) only carry
    a duration change, which is the best native fallback for the behavior line.
    """
    t = _walk(resp, "getPetHealthTrendsForPet") or {}
    out: dict[str, str] = {}
    aliases = {"UP": "up", "DOWN": "down", "STEADY": "steady", "SAME": "steady"}
    for tr in t.get("behaviorTrends") or []:
        if tr.get("disabled"):
            continue
        keys = TREND_KEYS.get(tr.get("title"))
        if not keys or keys[0] is None:
            continue
        sc = tr.get("summaryComponents") or {}
        change = sc.get("eventsChange") or sc.get("durationChange") or {}
        direction = aliases.get(str(change.get("direction") or "").upper())
        if direction:
            out[keys[0]] = direction
    return out


def _parse_timestamp(value: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def parse_rest_summaries(resp: dict, *, now: datetime | None = None) -> dict[str, float]:
    """Validated daily sleep+naps in minutes; Fi durations are seconds.

    Fi occasionally emits corrupt multi-week nap durations and partial current
    periods. Neither is allowed into the durable baseline.
    """
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    out: dict[str, float] = {}
    for s in _walk(resp, "restSummaries") or []:
        start = _parse_timestamp(s.get("start") or "")
        day = start.date().isoformat() if start else ""
        amounts = _walk(s, "sleepAmounts") or []
        if not day or not amounts or start + timedelta(days=1) > now:
            continue
        try:
            durations = [float(a["duration"]) for a in amounts]
        except (KeyError, TypeError, ValueError):
            continue
        has_sleep = any(a.get("type") == "SLEEP" and duration > 0
                        for a, duration in zip(amounts, durations))
        total = sum(durations) / 60
        if has_sleep and 0 < total <= MAX_REST_MINUTES_PER_DAY:
            out[day] = total
    return out


# -------------------------------------------------------------------- history
def load_history(path: Path) -> dict:
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return {}


def save_history(path: Path, hist: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(hist, indent=0, sort_keys=True))


def _record(hist: dict, metric: str, day: str, value: float) -> None:
    hist.setdefault(metric, {})[day] = value


def _prune(hist: dict) -> None:
    """Keep only the most recent HISTORY_KEEP_DAYS readings per metric."""
    for series in hist.values():
        for stale in sorted(series)[:-HISTORY_KEEP_DAYS]:
            del series[stale]


def sanitize_history(hist: dict) -> dict:
    """Remove impossible Fi sleep readings from an existing history in place."""
    sleep = hist.get("sleep")
    if isinstance(sleep, dict):
        for day, value in list(sleep.items()):
            try:
                valid = 0 < float(value) <= MAX_REST_MINUTES_PER_DAY
            except (TypeError, ValueError):
                valid = False
            if not valid:
                del sleep[day]
    return hist


def update_history(path: Path, metric: str, day: str, value: float) -> None:
    hist = load_history(path)
    _record(hist, metric, day, value)
    series = hist[metric]
    for stale in sorted(series)[:-HISTORY_KEEP_DAYS]:
        del series[stale]
    save_history(path, hist)


# --- durable (gist-backed) history, for ephemeral sandboxes --------------------
def _gist_url(gist_id: str) -> str:
    return f"https://api.github.com/gists/{gist_id}"


def _gist_headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
            "User-Agent": "daily-digest"}


def gist_load(gist_id: str, token: str, *, timeout: float = 30.0) -> dict:
    """The history dict stored in the gist's bella_history.json (—> {} if empty/new)."""
    req = urllib.request.Request(_gist_url(gist_id), headers=_gist_headers(token))
    with urllib.request.urlopen(req, timeout=timeout) as r:
        obj = json.load(r)
    content = ((obj.get("files") or {}).get(GIST_FILE) or {}).get("content") or "{}"
    try:
        return json.loads(content)
    except (json.JSONDecodeError, TypeError):
        return {}


def gist_save(gist_id: str, token: str, hist: dict, *, timeout: float = 30.0) -> None:
    body = json.dumps({"files": {GIST_FILE: {"content": json.dumps(hist, sort_keys=True)}}}).encode()
    req = urllib.request.Request(_gist_url(gist_id), data=body, method="PATCH",
                                 headers={**_gist_headers(token), "Content-Type": "application/json"})
    urllib.request.urlopen(req, timeout=timeout).close()


def _history_store(env: dict, history_path: Path):
    """(load, save) bound to the configured store: the gist when BELLA_HISTORY_GIST
    + a token are set (ephemeral sandbox), else the local file. The gist round-trips
    history across runs that would otherwise lose the local file."""
    gist_id = env.get("BELLA_HISTORY_GIST")
    token = env.get("GITHUB_TOKEN") or env.get("BELLA_HISTORY_TOKEN")
    if gist_id and token:
        def load():
            try:
                return gist_load(gist_id, token)
            except Exception as exc:  # a store hiccup must not sink Bella's section
                logger.warning("bella: gist history load failed: %s", exc)
                return {}

        def save(hist):
            try:
                gist_save(gist_id, token, hist)
            except Exception as exc:
                logger.warning("bella: gist history save failed: %s", exc)

        return load, save
    return (lambda: load_history(history_path)), (lambda hist: save_history(history_path, hist))


# ---------------------------------------------------------------- composition
def build_section(today: date | None = None, *, env: dict | None = None,
                  history_path: Path = DEFAULT_HISTORY,
                  profile_path: Path = DEFAULT_PROFILE, gql=None,
                  pet_name: str | None = None) -> str | None:
    """Bella's rendered section, or None when there's no new data to show — Fi
    unconfigured/unreachable, or her collar feed frozen or empty. The digest
    drops the section entirely on None rather than surfacing a stale one."""
    today = today or date.today()
    env = os.environ if env is None else env
    pet_name = pet_name or env.get("FI_PET_NAME", "Bella")

    email, password = env.get("FI_EMAIL"), env.get("FI_PASSWORD")
    if gql is None and not (email and password):
        return None  # Fi not configured — nothing new to report

    try:
        if gql is None:
            gql = make_gql(email, password)
        pet_id = find_pet_id(gql("pets"), pet_name)
        if not pet_id:
            return None  # not on this Fi account — nothing to report
        steps_today = parse_daily_steps(gql("steps", pet_id=pet_id))
        sleep = parse_rest_summaries(gql("rest", pet_id=pet_id))
        try:
            trends_response = gql("trends", pet_id=pet_id)
            behaviors_today = parse_health_trends(trends_response)
            behavior_directions = parse_health_trend_directions(trends_response)
        except Exception as exc:  # behaviors are newer/less stable — don't sink the rest
            logger.warning("bella: behavior-trends fetch failed: %s", exc)
            behaviors_today = {}
            behavior_directions = {}
        try:
            save_profile(profile_path, parse_profile(gql("profile", pet_id=pet_id), today))
        except Exception as exc:
            logger.warning("bella: profile fetch failed: %s", exc)
    except Exception as exc:  # collar unreachable — no new data, so no section
        logger.warning("bella: fi fetch failed: %s", exc)
        return None

    # One load + one save per run (the store may be a remote gist). Steps and
    # behavior counts accumulate; sleep is a fresh multi-day window from Fi.
    load, save = _history_store(env, history_path)
    hist = sanitize_history(load())
    if steps_today is not None:
        _record(hist, "steps", today.isoformat(), steps_today)
    for day, minutes in sleep.items():  # the feed is shallow; accumulate for depth
        _record(hist, "sleep", day, minutes)
    for key, value in behaviors_today.items():
        _record(hist, key, today.isoformat(), value)
    _prune(hist)
    save(hist)
    series = {k: hist.get(k, {}) for k in
              ("steps", "sleep", *{ek for ek, dk in TREND_KEYS.values() if ek},
               *{dk for ek, dk in TREND_KEYS.values() if dk})}
    if not trends.has_fresh_data(series, today):
        return None  # collar feed frozen or empty — drop the section
    if not trends.has_readable_signal(series, today, behavior_directions):
        # Data arrived, but there's not yet enough history to read any trend —
        # the section would be all "baseline building" filler. Per Shawn: if
        # there's nothing meaningful from the collar, don't mention Bella at all.
        return None
    return trends.render_pet_section(pet_name, series, today, behavior_directions)
