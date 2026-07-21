#!/usr/bin/env python3
"""Live, watch-aware health section for the morning digest.

Reads CURRENT values straight from the self-hosted Health Auto Export (HAE)
server every run. Both data paths are freshness-guarded: when the watch has
been off, recovery metrics degrade to an explicit "watch off N days" line,
and when the iPhone stops pushing entirely, activity degrades to a "no phone
health data since <date>" line (📵) instead of rendering stale numbers as
current.

Stdlib only. Pure rendering (`render_section`) is separated from network I/O
(`build_section`) so the watch-state / degradation logic is unit-testable.
"""
from __future__ import annotations

import json
import logging
import os
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from statistics import mean, pstdev
from typing import Callable

logger = logging.getLogger(__name__)

LOOKBACK_DAYS = 30
WRIST_FRESH_DAYS = 2        # watch metrics older than this read as "off-wrist"
PHONE_FRESH_DAYS = 2        # activity metrics older than this read as "phone not pushing"
MIN_BASELINE_SAMPLES = 7    # need ~a week before z-scores mean anything
RECENT_WINDOW_DAYS = 3

# (metric_id, aggregation, label, value format)
ACTIVITY_METRICS = (
    ("step_count", "sum", "steps", "{:,.0f}"),
    ("active_energy", "sum", "kcal", "{:,.0f}"),
    ("apple_exercise_time", "sum", "min exercise", "{:.0f}"),
)
WRIST_GAP_METRICS = ("heart_rate_variability", "resting_heart_rate")
DEFAULT_CONNECTOR = Path.home() / ".openjarvis" / "connectors" / "apple_health_remote.json"


# ----------------------------------------------------------------- config + I/O
def load_hae_config() -> tuple[str, str]:
    """(base_url, token). Prefers HAE_BASE_URL / HAE_READ_TOKEN env vars, else
    falls back to the Apple Health connector JSON (HAE_CONNECTOR_JSON or the
    openjarvis default location on neb-server)."""
    base = os.environ.get("HAE_BASE_URL", "")
    token = os.environ.get("HAE_READ_TOKEN", "")
    if base and token:
        return base, token
    path = Path(os.environ.get("HAE_CONNECTOR_JSON", str(DEFAULT_CONNECTOR)))
    try:
        cfg = json.loads(path.read_text())
    except (OSError, ValueError):
        # No env vars and no readable connector file (e.g. a cloud sandbox with
        # no HAE tunnel) — report "unconfigured" rather than crash. The caller
        # treats empty creds as "no health data", so the You section just drops.
        cfg = {}
    return (
        base or cfg.get("base_url") or cfg.get("url") or "",
        token or cfg.get("read_token") or cfg.get("token") or "",
    )


def fetch_metric(*, base_url: str, token: str, metric: str,
                 days: int = LOOKBACK_DAYS, timeout: float = 30.0) -> list[dict]:
    """Raw HAE rows for one metric over the last `days` days (HAE start/end params)."""
    now = datetime.now(timezone.utc)
    qs = urllib.parse.urlencode({
        "start": (now - timedelta(days=days)).strftime("%Y-%m-%d"),
        "end": (now + timedelta(days=1)).strftime("%Y-%m-%d"),
    })
    url = f"{base_url.rstrip('/')}/api/metrics/{metric}?{qs}"
    req = urllib.request.Request(url, headers={"api-key": token})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.load(resp)
    if isinstance(data, list):
        return data
    return data.get("data") or data.get("metrics") or []


# -------------------------------------------------------------------- analysis
def aggregate_daily(rows: list[dict], how: str) -> dict[str, float]:
    """Bucket raw HAE rows into one value per UTC day."""
    buckets: dict[str, list[tuple[str, float]]] = {}
    for row in rows:
        ds, qty = row.get("date"), row.get("qty")
        if not ds or qty is None:
            continue
        buckets.setdefault(ds[:10], []).append((ds, float(qty)))
    out: dict[str, float] = {}
    for day, items in buckets.items():
        vals = [v for _, v in items]
        if how == "sum":
            out[day] = sum(vals)
        elif how == "avg":
            out[day] = sum(vals) / len(vals)
        elif how in ("last", "min_overnight"):
            out[day] = max(items, key=lambda iv: iv[0])[1]
        else:
            raise ValueError(f"Unknown aggregation: {how}")
    return out


def per_metric_finding(daily: dict[str, float], *, freshness_hours: int = 24) -> dict:
    """z-score of the recent window vs baseline, plus freshness, for one metric."""
    days = sorted(daily)
    if len(days) < MIN_BASELINE_SAMPLES:
        return {"status": "insufficient_data", "days_with_data": len(days), "stale": True}
    recent = days[-RECENT_WINDOW_DAYS:]
    baseline = [daily[d] for d in days[:-RECENT_WINDOW_DAYS]]
    recent_vals = [daily[d] for d in recent]
    bs_mean = mean(baseline) if baseline else 0.0
    bs_sd = pstdev(baseline) if len(baseline) > 1 else 0.0
    rc_mean = mean(recent_vals) if recent_vals else 0.0
    z = (rc_mean - bs_mean) / bs_sd if bs_sd > 0 else 0.0
    most_recent = datetime.fromisoformat(days[-1] + "T00:00:00+00:00")
    age_h = (datetime.now(timezone.utc) - most_recent).total_seconds() / 3600
    return {"baseline_mean": bs_mean, "recent_mean": rc_mean, "z_score": z,
            "stale": age_h > freshness_hours}


def _days_since_last(daily: dict[str, float], today: date) -> int | None:
    if not daily:
        return None
    return (today - date.fromisoformat(max(daily))).days


def _recovery_word(z: float) -> str:
    if z >= 0.5:
        return "well recovered"
    if z >= -0.5:
        return "normal"
    if z >= -1.0:
        return "slightly under-recovered"
    return "under-recovered"


def _recent_avg(daily: dict[str, float], days: int = 7) -> float | None:
    if not daily:
        return None
    recent = [daily[d] for d in sorted(daily)[-days:]]
    return sum(recent) / len(recent) if recent else None


def _trend_arrow(daily: dict[str, float], days: int = 7) -> str:
    """↗ / ↘ / → over the last `days` (regression-slope sign, ≥1%/day to count)."""
    items = sorted(daily.items())[-days:]
    if len(items) < 3:
        return ""
    n = len(items)
    xs = list(range(n))
    ys = [v for _, v in items]
    mx = sum(xs) / n
    my = sum(ys) / n
    num = sum((xs[i] - mx) * (ys[i] - my) for i in range(n))
    den = sum((x - mx) ** 2 for x in xs) or 1.0
    slope = num / den
    if not my or abs(slope) / abs(my) < 0.01:
        return "→"
    return "↗" if slope > 0 else "↘"


def _activity_label(today_val: float, avg: float) -> str:
    if not avg:
        return ""
    r = today_val / avg
    if r < 0.5:
        return "light day"
    if r < 0.8:
        return "below average"
    if r < 1.2:
        return "average day"
    if r < 1.5:
        return "active day"
    return "high-activity day"


def _rhr_label(delta: float) -> str:
    """Resting-HR delta vs baseline → plain meaning (lower = better recovery)."""
    if delta <= -3:
        return "good recovery"
    if delta <= 0:
        return "slightly low (good)"
    if delta <= 3:
        return "normal"
    if delta <= 6:
        return "slightly elevated"
    return "elevated — possible stress"


def _spo2_label(v: float) -> str:
    if v >= 95:
        return "normal"
    if v >= 92:
        return "slightly low"
    return "low — recheck"


def _hrv_trend_text(arrow: str) -> str:
    return {"↗": "rising 7d", "↘": "falling 7d", "→": "stable 7d"}.get(arrow, "")


def _rhr_trend_text(arrow: str) -> str:
    # for resting HR, lower = better — annotate direction in those terms
    return {"↗": "rising 7d (less recovery)",
            "↘": "falling 7d (favorable)",
            "→": "stable 7d"}.get(arrow, "")


def _nights_logged(daily: dict[str, float], days: int, today: date) -> int:
    cutoff = (today - timedelta(days=days - 1)).isoformat()
    return sum(1 for d in daily if d >= cutoff)


def _value_n_days_ago(daily: dict[str, float], today: date, n: int) -> float | None:
    """The most recent recorded value on or before (today - n days)."""
    if not daily:
        return None
    target = (today - timedelta(days=n)).isoformat()
    candidates = [d for d in daily if d <= target]
    if not candidates:
        return None
    return daily[max(candidates)]


def _signed_int(delta: float) -> str:
    """'+2' / '-2' / '0' — no '+0' / '-0' noise."""
    return f"{delta:+.0f}" if delta else "0"


def _signed_pct(delta: float, base: float) -> str:
    if not base:
        return "—"
    return f"{delta / base * 100:+.0f}%"


def staleness_note(daily_by_metric: dict[str, dict[str, float]], today: date,
                   *, fresh_days: int = 1) -> str | None:
    """📵 note explaining that iPhone activity has stopped arriving; None while
    fresh. The default threshold (gap > 1 day) complements the digest's
    has_fresh_data gate (STALE_AFTER_DAYS = 2) so build_digest swaps its
    dropped You section for this note the moment the feed freezes;
    render_section passes PHONE_FRESH_DAYS for the legacy numeric section."""
    gaps = [
        g for g in (
            _days_since_last(daily_by_metric.get(m, {}), today)
            for m, _how, _label, _fmt in ACTIVITY_METRICS
        )
        if g is not None
    ]
    gap = min(gaps) if gaps else None
    if gap is None:
        return f"📵 No phone health data in the last {LOOKBACK_DAYS} days — check Health Auto Export."
    if gap <= fresh_days:
        return None
    last_date = today - timedelta(days=gap)
    last_iso = last_date.isoformat()
    parts = [
        f"{fmt.format(d[max(d)])} {label}"
        for metric, _how, label, fmt in ACTIVITY_METRICS
        if (d := daily_by_metric.get(metric, {})) and max(d) == last_iso
    ]
    note = (
        f"📵 No phone health data for {gap} days (last data {last_date:%b %-d}) — "
        f"open Health Auto Export on the iPhone and re-run its automation."
    )
    if parts:
        note += f"\n   Last activity ({last_date:%b %-d}): " + " · ".join(parts)
    return note


# --------------------------------------------------------------------- render
def render_section(daily_by_metric: dict[str, dict[str, float]], today: date) -> str:
    """Render the health section. Pure.

    One block per stat: a header line with the value + plain-English meaning,
    then a *comparisons* line that names its reference points explicitly —
    `vs yesterday X (±Δ)` and `vs week ago Y (±Δ)` — so the reader never has to
    guess what the trend is being measured against.

    The activity block has three states keyed off phone freshness: fresh
    (≤ PHONE_FRESH_DAYS: full value + comparison lines), stale (📵 line with
    the last-data date + a last-known snapshot), and empty (📵 no data in the
    lookback window).
    """
    L: list[str] = [f"\U0001f4aa Health — {today:%a %b %-d}", ""]

    # ---------- Activity (iPhone-sourced; freshness-guarded) ----------
    steps = daily_by_metric.get("step_count", {})
    phone_gaps = [
        g for g in (
            _days_since_last(daily_by_metric.get(m, {}), today)
            for m, _how, _label, _fmt in ACTIVITY_METRICS
        )
        if g is not None
    ]
    phone_gap = min(phone_gaps) if phone_gaps else None

    if phone_gap is None or phone_gap > PHONE_FRESH_DAYS:
        L.append(staleness_note(daily_by_metric, today, fresh_days=PHONE_FRESH_DAYS))
        L.append("")
    else:
        activity_day = max(steps) if steps else None
        parts: list[str] = []
        for metric, _how, label, fmt in ACTIVITY_METRICS:
            d = daily_by_metric.get(metric, {})
            if d:
                parts.append(f"{fmt.format(d[max(d)])} {label}")
        if parts:
            day_lbl = f" ({date.fromisoformat(activity_day):%a})" if activity_day else ""
            today_steps = steps[max(steps)] if steps else 0
            avg7 = _recent_avg(steps, 7) or 0
            label = _activity_label(today_steps, avg7) if avg7 else ""
            header = f"*Activity{day_lbl}:* " + " · ".join(parts)
            if label:
                header += f"  _{label}_"
            L.append(header)
            if steps:
                comps: list[str] = []
                y = _value_n_days_ago(steps, today, 1)
                w = _value_n_days_ago(steps, today, 7)
                if y is not None:
                    comps.append(f"vs yesterday {y:,.0f} ({_signed_pct(today_steps - y, y)})")
                if w is not None:
                    comps.append(f"vs week ago {w:,.0f} ({_signed_pct(today_steps - w, w)})")
                if comps:
                    L.append("   " + "  ·  ".join(comps))
            L.append("")

    # ---------- Watch state drives recovery + sleep ----------
    gaps = [
        g for g in (_days_since_last(daily_by_metric.get(m, {}), today) for m in WRIST_GAP_METRICS)
        if g is not None
    ]
    wrist_gap = min(gaps) if gaps else None

    if wrist_gap is not None and wrist_gap <= WRIST_FRESH_DAYS:
        # ---- HRV (higher = better recovery) ----
        hrv = daily_by_metric.get("heart_rate_variability", {})
        if hrv:
            today_val = hrv[max(hrv)]
            f_hrv = per_metric_finding(hrv, freshness_hours=24 * (WRIST_FRESH_DAYS + 1))
            word = _recovery_word(f_hrv["z_score"]) if "z_score" in f_hrv else ""
            header = f"*HRV:* {today_val:.0f} ms"
            if word:
                header += f"  _{word}_"
            L.append(header)
            comps = []
            y = _value_n_days_ago(hrv, today, 1)
            w = _value_n_days_ago(hrv, today, 7)
            if y is not None:
                comps.append(f"vs yesterday {y:.0f} ms ({_signed_int(today_val - y)} ms)")
            if w is not None:
                comps.append(f"vs week ago {w:.0f} ms ({_signed_int(today_val - w)} ms)")
            if comps:
                L.append("   " + "  ·  ".join(comps))
            L.append("")

        # ---- Resting HR (lower = better recovery) ----
        rhr = daily_by_metric.get("resting_heart_rate", {})
        if rhr:
            today_val = rhr[max(rhr)]
            f_rhr = per_metric_finding(rhr, freshness_hours=24 * (WRIST_FRESH_DAYS + 1))
            word = ""
            if f_rhr.get("baseline_mean"):
                word = _rhr_label(f_rhr["recent_mean"] - f_rhr["baseline_mean"])
            header = f"*Resting HR:* {today_val:.0f} bpm"
            if word:
                header += f"  _{word}_"
            L.append(header)
            comps = []
            y = _value_n_days_ago(rhr, today, 1)
            w = _value_n_days_ago(rhr, today, 7)
            if y is not None:
                comps.append(f"vs yesterday {y:.0f} bpm ({_signed_int(today_val - y)} bpm)")
            if w is not None:
                comps.append(f"vs week ago {w:.0f} bpm ({_signed_int(today_val - w)} bpm)")
            if comps:
                L.append("   " + "  ·  ".join(comps))
            L.append("")

        # ---- SpO₂ ----
        spo2 = daily_by_metric.get("blood_oxygen_saturation", {})
        if spo2:
            v = spo2[max(spo2)]
            L.append(f"*SpO₂:* {v:.0f}%  _{_spo2_label(v)}_  (normal range 95–100%)")
            L.append("")

        # ---- Sleep ----
        sleep = daily_by_metric.get("sleep_analysis", {})
        s_gap = _days_since_last(sleep, today)
        nights = _nights_logged(sleep, 7, today)
        if s_gap is not None and s_gap <= 1:
            L.append(f"*Sleep:* {sleep[max(sleep)]:.1f} h last night  ·  {nights}/7 nights logged this week")
        else:
            L.append(f"*Sleep:* not recorded last night  ·  {nights}/7 nights logged this week  _watch off overnight_")
        L.append("")

        L.append("⌚ Watch on")
    else:
        if wrist_gap is None:
            L.append("⌚ No recent Apple Watch data — recovery & sleep unavailable.")
        else:
            L.append(
                f"⌚ Watch off {wrist_gap} days — recovery (HRV/resting HR) & sleep paused "
                f"until you wear it (auto-resumes; ~7-day baseline rebuild)."
            )
            hrv = daily_by_metric.get("heart_rate_variability", {})
            if hrv:
                d = max(hrv)
                L.append(f"   Last HRV ({date.fromisoformat(d):%b %-d}): {hrv[d]:.0f} ms")
    return "\n".join(L)


def fetch_daily_by_metric(*, fetch: Callable = fetch_metric,
                          config: Callable[[], tuple[str, str]] = load_hae_config,
                          ) -> dict[str, dict[str, float]]:
    """Fetch live values from HAE for every digest metric. Resilient per-metric."""
    base, token = config()
    if not (base and token):
        return {}  # HAE not configured (no env, no connector file) -> You drops
    wanted = (
        ("step_count", "sum"),
        ("active_energy", "sum"),
        ("apple_exercise_time", "sum"),
        ("heart_rate_variability", "avg"),
        ("resting_heart_rate", "avg"),
        ("blood_oxygen_saturation", "min_overnight"),
        ("sleep_analysis", "sum"),
    )
    daily_by_metric: dict[str, dict[str, float]] = {}
    for metric, how in wanted:
        try:
            rows = fetch(base_url=base, token=token, metric=metric, days=LOOKBACK_DAYS)
            daily_by_metric[metric] = aggregate_daily(rows, how)
        except Exception as exc:  # one bad metric must not sink the whole digest
            logger.warning("health: fetch failed for %s: %s", metric, exc)
            daily_by_metric[metric] = {}
    return daily_by_metric


def build_section(today: date | None = None, *,
                  fetch: Callable = fetch_metric,
                  config: Callable[[], tuple[str, str]] = load_hae_config) -> str:
    """Fetch live values from HAE and render the (legacy, numeric) section."""
    today = today or datetime.now(timezone.utc).date()
    return render_section(fetch_daily_by_metric(fetch=fetch, config=config), today)


def main() -> int:
    print(build_section())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
