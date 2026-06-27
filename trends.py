"""Trends-only digest sections — directions, never numbers (one exception).

The digest's job is one sentence per signal: is it going up, down, or holding
steady, and does any combination hint at stress, sleep debt, or oncoming
illness. Raw values stay out of the rendering (the date header is one place a
digit may appear). The deliberate exception, by Shawn's request: Bella's
Series 3 behavior lines (eating / drinking / scratching / licking / barking)
show her actual event count alongside the direction. Her activity (steps) and
rest stay numberless like everything else.

Works on the same `{metric: {iso_day: value}}` shape that health.aggregate_daily
produces, so it plugs into the existing HAE fetch path and the Fi (pet) fetch
path alike. Pure rendering — no network I/O in this module.
"""
from __future__ import annotations

from datetime import date, timedelta

RECENT_DAYS = 3            # the window we call "now"
BASELINE_DAYS = 14         # what we call "your usual"
MIN_BASELINE_SAMPLES = 4   # fewer than this and a trend is noise
TREND_THRESHOLD = 0.08     # relative move below this reads as steady
SHARP_THRESHOLD = 0.15     # relative move above this is a loud signal
STALE_AFTER_DAYS = 2       # yesterday-only data is normal for a morning run
SPO2_NORMAL_FLOOR = 95.0
MIN_NIGHTS_PER_WEEK = 4


# ----------------------------------------------------------------- primitives
def _recent_and_baseline(daily: dict[str, float]) -> tuple[list[float], list[float]]:
    days = sorted(daily)
    recent = [daily[d] for d in days[-RECENT_DAYS:]]
    baseline = [daily[d] for d in days[-(RECENT_DAYS + BASELINE_DAYS):-RECENT_DAYS]]
    return recent, baseline


def _relative_move(daily: dict[str, float]) -> float | None:
    recent, baseline = _recent_and_baseline(daily)
    if len(baseline) < MIN_BASELINE_SAMPLES or not recent:
        return None
    b = sum(baseline) / len(baseline)
    if not b:
        return None
    r = sum(recent) / len(recent)
    return (r - b) / b


def classify_trend(daily: dict[str, float], today: date) -> str | None:
    """'up' / 'down' / 'steady', or None when there isn't enough history."""
    move = _relative_move(daily)
    if move is None:
        return None
    if move > TREND_THRESHOLD:
        return "up"
    if move < -TREND_THRESHOLD:
        return "down"
    return "steady"


def is_sharp_move(daily: dict[str, float], today: date) -> bool:
    move = _relative_move(daily)
    return move is not None and abs(move) > SHARP_THRESHOLD


def two_point_direction(daily: dict[str, float]) -> str | None:
    """Latest vs the immediately-previous reading. The fallback when there
    isn't enough history for a statistical trend — gives a usable direction
    from just two days instead of waiting a week."""
    days = sorted(daily)
    if len(days) < 2:
        return None
    prev, last = daily[days[-2]], daily[days[-1]]
    if prev == 0:
        return "up" if last > 0 else "steady"
    move = (last - prev) / prev
    if move > TREND_THRESHOLD:
        return "up"
    if move < -TREND_THRESHOLD:
        return "down"
    return "steady"


def direction(daily: dict[str, float], today: date) -> str | None:
    """Best available read: a statistical trend when there's baseline depth,
    otherwise a simple previous-vs-latest comparison. None only when there's
    fewer than two readings."""
    t = classify_trend(daily, today)
    if t is not None:
        return t
    return two_point_direction(daily)


def staleness_days(daily_by_metric: dict[str, dict[str, float]], today: date) -> int | None:
    """Days since the newest datapoint across all metrics; None if no data."""
    newest = max((max(d) for d in daily_by_metric.values() if d), default=None)
    if newest is None:
        return None
    return (today - date.fromisoformat(newest)).days


def has_fresh_data(daily_by_metric: dict[str, dict[str, float]], today: date) -> bool:
    """True when the feed carries new data — its newest datapoint is within the
    freshness window (today or yesterday). A frozen feed (synced days ago) or an
    empty one returns False, which the digest uses to drop the section entirely
    rather than re-render stale trends as if they were fresh."""
    age = staleness_days(daily_by_metric, today)
    return age is not None and age < STALE_AFTER_DAYS


def _recent_mean(daily: dict[str, float]) -> float | None:
    recent, _ = _recent_and_baseline(daily)
    return sum(recent) / len(recent) if recent else None


def _stale_warning(noun: str, daily_by_metric: dict, today: date) -> str | None:
    age = staleness_days(daily_by_metric, today)
    if age is None or age < STALE_AFTER_DAYS:
        return None
    newest = max(max(d) for d in daily_by_metric.values() if d)
    when = date.fromisoformat(newest)
    since = "in over a week" if age >= 7 else f"since {when:%A}"
    return f"⚠️ {noun} hasn't synced {since} — trends below are from the last sync."


# ------------------------------------------------------------------ you
def _activity_line(d: dict[str, dict[str, float]], today: date) -> str:
    directions = [
        t for m in ("step_count", "active_energy", "apple_exercise_time")
        if (t := direction(d.get(m, {}), today)) is not None
    ]
    if not directions:
        return "• Activity & exercise: no recent movement data."
    top = max(("up", "down", "steady"), key=directions.count)
    return {
        "up": "• Activity & exercise: trending up — more movement and exercise than your usual.",
        "down": "• Activity & exercise: trending down — less movement than your usual.",
        "steady": "• Activity & exercise: steady — about your usual level.",
    }[top]


def _lungs_line(d: dict[str, dict[str, float]], today: date) -> tuple[str, bool]:
    """(line, below_normal_flag) — the flag feeds the illness watch."""
    spo2 = d.get("blood_oxygen_saturation", {})
    if not spo2:
        return "• Lungs: no recent blood-oxygen reading.", False
    rm = _recent_mean(spo2)
    if rm is not None and rm < SPO2_NORMAL_FLOOR:
        return "• Lungs: blood oxygen below your normal range — keep an eye on it.", True
    t = direction(spo2, today)
    if t == "down":
        return "• Lungs: blood oxygen drifting lower, still in the normal range.", False
    return "• Lungs: blood oxygen steady, in your normal range.", False


def _stress_signals(d: dict[str, dict[str, float]], today: date) -> tuple[bool, bool]:
    """(hrv_down, rhr_up) — the two strain markers."""
    return (
        direction(d.get("heart_rate_variability", {}), today) == "down",
        direction(d.get("resting_heart_rate", {}), today) == "up",
    )


def _stress_line(hrv_down: bool, rhr_up: bool) -> str:
    if hrv_down and rhr_up:
        return ("• Stress: signs of stress building — recovery dipping and "
                "resting heart rate creeping up.")
    if hrv_down or rhr_up:
        return "• Stress: mild strain showing — one recovery marker moving the wrong way."
    return "• Stress: no signs of stress — recovery looks steady."


def _sleep_line(d: dict[str, dict[str, float]], today: date) -> str:
    sleep = d.get("sleep_analysis", {})
    if not sleep:
        return "• Sleep: watch not worn overnight — no sleep signal this week."
    cutoff = (today - timedelta(days=6)).isoformat()
    nights = sum(1 for day in sleep if day >= cutoff)
    if nights < MIN_NIGHTS_PER_WEEK:
        return "• Sleep: patchy — watch off most nights, not enough to read a trend."
    t = direction(sleep, today)
    if t == "down":
        return "• Sleep: running short — most nights below your usual."
    if t == "up":
        return "• Sleep: getting more than usual — good."
    return "• Sleep: on track — about your usual."


def _illness_line(d: dict[str, dict[str, float]], today: date, lungs_flag: bool) -> str:
    hrv = d.get("heart_rate_variability", {})
    rhr = d.get("resting_heart_rate", {})
    signals = sum((
        classify_trend(hrv, today) == "down" and is_sharp_move(hrv, today),
        classify_trend(rhr, today) == "up" and is_sharp_move(rhr, today),
        lungs_flag or classify_trend(d.get("blood_oxygen_saturation", {}), today) == "down",
    ))
    if signals >= 2:
        return ("• Illness watch: possible signs you're fighting something off — "
                "take it easy today.")
    return "• Illness watch: no signals you're coming down with something."


def render_you_section(daily_by_metric: dict[str, dict[str, float]], today: date) -> str:
    L = [f"💪 You — {today:%a %b %-d}", ""]
    if not any(daily_by_metric.values()):
        L.append("No health data available — is the export running?")
        return "\n".join(L)
    warning = _stale_warning("Health data", daily_by_metric, today)
    if warning:
        L += [warning, ""]
    lungs, lungs_flag = _lungs_line(daily_by_metric, today)
    hrv_down, rhr_up = _stress_signals(daily_by_metric, today)
    L += [
        _activity_line(daily_by_metric, today),
        lungs,
        _stress_line(hrv_down, rhr_up),
        _sleep_line(daily_by_metric, today),
        _illness_line(daily_by_metric, today, lungs_flag),
    ]
    return "\n".join(L)


# ------------------------------------------------------------------ pet
def render_pet_section(name: str, series: dict[str, dict[str, float]], today: date) -> str:
    L = [f"🐕 {name}", ""]
    if not any(series.values()):
        L.append(f"No data from {name}'s Fi collar yet.")
        return "\n".join(L)
    warning = _stale_warning(f"{name}'s collar", series, today)
    if warning:
        L += [warning, ""]
    steps = series.get("steps", {})
    t = direction(steps, today)
    if not steps:
        L.append("• Activity: no recent movement data from the collar.")
    else:
        L.append({
            "up": "• Activity: trending up — moving more than her usual.",
            "down": "• Activity: trending down — moving less than her usual.",
            "steady": "• Activity: steady — right around her usual daily movement.",
            None: "• Activity: not enough history yet to read a trend.",
        }[t])
    sleep = series.get("sleep", {})
    if sleep:
        t = direction(sleep, today)
        L.append({
            "up": "• Sleep: resting more than usual the past few days.",
            "down": "• Sleep: resting less than usual — could mean restlessness.",
            "steady": "• Sleep: normal — her usual rest pattern.",
            None: "• Sleep: not enough history yet to read a trend.",
        }[t])

    # ---- Series 3+ AI behaviors: real event count + health-flavored direction ----
    for key, label, up, down, steady in PET_BEHAVIORS:
        d = series.get(key, {})
        if not d:
            continue
        count = _event_count(d)
        t = direction(d, today)
        if t is None:  # first reading — tracked, just nothing to compare against yet
            L.append(f"• {label}: {count} today — tracking started, baseline building.")
            continue
        phrasing = {"up": up, "down": down, "steady": steady}[t]
        L.append(f"• {label}: {count} today, {phrasing}")
    return "\n".join(L)


def _event_count(daily: dict[str, float]) -> str:
    """Bella's most recent behavior reading as 'N event(s)' — the one place
    numbers are allowed in a pet section, per Shawn's request."""
    n = int(round(daily[sorted(daily)[-1]]))
    return f"{n:,} event" + ("" if n == 1 else "s")


# (series key, label, up phrasing, down phrasing, steady phrasing) — health-aware.
PET_BEHAVIORS = (
    ("eating_events", "Eating",
     "eating more often than usual — good appetite.",
     "eating less often than usual — watch her appetite.",
     "eating about as usual."),
    ("drinking_events", "Drinking",
     "drinking more than usual — worth noting if it keeps climbing.",
     "drinking less than usual — keep an eye on her water intake.",
     "drinking about as usual."),
    ("scratching_events", "Scratching",
     "scratching more than usual — possible itch, skin, or fleas.",
     "scratching less than usual — good.",
     "scratching about as usual."),
    ("licking_events", "Licking",
     "licking more than usual — can signal irritation, allergies, or stress.",
     "licking less than usual — good.",
     "licking about as usual."),
    ("barking_events", "Barking",
     "barking more than usual — more alert or unsettled than normal.",
     "barking less than usual — calmer than normal.",
     "barking about as usual."),
)
