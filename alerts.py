"""Health alerting — troublesome patterns in Shawn's or Bella's data → Telegram.

Deterministic detectors built on the same trend primitives as the digest
(testable, no LLM in the alert path). Edge-triggered per the fleet-wide
convention: an alert fires when a condition APPEARS, stays quiet while it
persists, and re-fires only after it clears and comes back.

Delivery is `hermes send -t telegram` — reuses the Hermes gateway's bot
credentials, no agent loop.
"""
from __future__ import annotations

import json
import logging
import subprocess
from datetime import date
from pathlib import Path

import trends

logger = logging.getLogger(__name__)

HERMES_BIN = str(Path.home() / ".local" / "bin" / "hermes")
STATE_PATH = Path.home() / ".daily-digest" / "active_alerts.json"


# ----------------------------------------------------------------- detectors
def detect_alerts(daily_by_metric: dict, bella_series: dict, today: date) -> list[str]:
    """Plain-English alert lines (no numbers). Empty list = all clear."""
    found: list[str] = []

    hrv = daily_by_metric.get("heart_rate_variability", {})
    rhr = daily_by_metric.get("resting_heart_rate", {})
    spo2 = daily_by_metric.get("blood_oxygen_saturation", {})
    sleep = daily_by_metric.get("sleep_analysis", {})

    hrv_crash = trends.classify_trend(hrv, today) == "down" and trends.is_sharp_move(hrv, today)
    rhr_spike = trends.classify_trend(rhr, today) == "up" and trends.is_sharp_move(rhr, today)
    spo2_recent = trends._recent_mean(spo2)
    spo2_low = spo2_recent is not None and spo2_recent < trends.SPO2_NORMAL_FLOOR

    if sum((hrv_crash, rhr_spike, spo2_low)) >= 2:
        found.append("You: recovery and oxygen are moving the wrong way together — "
                     "possible sign you're fighting something off. Take it easy and "
                     "keep an eye on how you feel.")
    else:
        if spo2_low:
            found.append("You: blood oxygen has dropped below your normal range — "
                         "worth watching, recheck after a calm hour.")
        if rhr_spike:
            found.append("You: resting heart rate has risen sharply vs your usual — "
                         "could be stress, poor sleep, or an oncoming bug.")
        if hrv_crash:
            found.append("You: recovery (HRV) has dropped sharply vs your usual — "
                         "your body is under more strain than normal.")

    if trends.classify_trend(sleep, today) == "down" and trends.is_sharp_move(sleep, today):
        found.append("You: sleep has fallen sharply below your usual — "
                     "you're building a sleep debt.")

    b_steps = bella_series.get("steps", {})
    b_sleep = bella_series.get("sleep", {})
    if trends.classify_trend(b_steps, today) == "down" and trends.is_sharp_move(b_steps, today):
        found.append("Bella: her movement has dropped sharply vs her usual — "
                     "could be soreness, low energy, or feeling off.")
    if trends.classify_trend(b_sleep, today) == "up" and trends.is_sharp_move(b_sleep, today):
        found.append("Bella: she's resting a lot more than usual — "
                     "possible lethargy, watch her appetite and energy.")
    if trends.classify_trend(b_sleep, today) == "down" and trends.is_sharp_move(b_sleep, today):
        found.append("Bella: she's resting much less than usual — "
                     "possible restlessness or discomfort.")

    return found


# -------------------------------------------------------------- edge trigger
def edge_filter(current: list[str], state_path: Path = STATE_PATH) -> list[str]:
    """Only alerts whose condition newly appeared since the last run."""
    try:
        previous = set(json.loads(state_path.read_text()))
    except (OSError, ValueError):
        previous = set()
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps(sorted(current)))
    return [a for a in current if a not in previous]


# ------------------------------------------------------------------ delivery
def send_telegram(alert_lines: list[str], *, run=subprocess.run) -> None:
    if not alert_lines:
        return
    body = "\n\n".join(alert_lines)
    r = run([HERMES_BIN, "send", "-t", "telegram",
             "-s", "🩺 Health alert", body],
            capture_output=True, text=True, timeout=60)
    if r.returncode != 0:
        logger.warning("alerts: telegram send failed: %s", r.stderr[-200:])
