#!/usr/bin/env python3
"""Personal morning digest — trends only, never numbers.

Two sections: Shawn's health trends (live HAE) and Bella's health trends
(Fi Series 3 collar). Each line says increasing / decreasing / steady plus
what it hints at (stress, sleep debt, getting sick) — raw values stay out.

Stdlib only. Runs wherever HAE is reachable (neb-brain-hostinger) with
FI_EMAIL / FI_PASSWORD in the environment for Bella's section.
"""
from __future__ import annotations

import json
import os
import urllib.request
from datetime import datetime
from zoneinfo import ZoneInfo

import alerts
import bella
import health
import llm
import trends

PT = ZoneInfo("America/Los_Angeles")
_UNSET = object()


def build_digest(today=None, *, daily_by_metric=None, bella_section=None,
                 llm_body=_UNSET) -> str:
    today = today or datetime.now(PT).date()
    if daily_by_metric is None:
        daily_by_metric = health.fetch_daily_by_metric()
    if bella_section is None:
        bella_section = bella.build_section(today)

    if llm_body is _UNSET:
        # Snapshot this export, compare against the previous one via the LLM.
        bella_series = bella.load_history(bella.DEFAULT_HISTORY)
        bella_profile = bella.load_profile(bella.DEFAULT_PROFILE)
        previous = llm.load_previous_snapshot(llm.SNAPSHOT_DIR, today)
        llm.save_snapshot(llm.SNAPSHOT_DIR, today, daily_by_metric, bella_series)
        current = {"date": today.isoformat(), "you": daily_by_metric,
                   "bella": bella_series}
        if bella_profile:
            current["bella_profile"] = bella_profile
        llm_body = llm.generate_digest(current, previous, today)

    header = f"☀️  Morning Digest — {today:%A, %B %-d, %Y}"
    if llm_body:
        return "\n".join([header, "", llm_body])
    # deterministic fallback — guaranteed numberless
    return "\n".join([
        header,
        "",
        trends.render_you_section(daily_by_metric, today),
        "",
        bella_section,
    ])


def _send_slack(text: str) -> None:
    """Post to Slack if SLACK_BOT_TOKEN + SLACK_CHANNEL are configured."""
    body = json.dumps({"channel": os.environ["SLACK_CHANNEL"],
                       "text": text, "mrkdwn": True}).encode()
    req = urllib.request.Request(
        "https://slack.com/api/chat.postMessage", data=body, method="POST",
        headers={"Authorization": f"Bearer {os.environ['SLACK_BOT_TOKEN']}",
                 "Content-Type": "application/json"})
    r = json.load(urllib.request.urlopen(req, timeout=20))
    if not r.get("ok"):
        raise SystemExit(f"slack post failed: {r.get('error')}")


def run_alerts(daily_by_metric, bella_series, today, *,
               send=alerts.send_telegram, state_path=alerts.STATE_PATH) -> None:
    """Detect troublesome patterns and Telegram only the newly-appeared ones."""
    new = alerts.edge_filter(alerts.detect_alerts(daily_by_metric, bella_series, today),
                             state_path)
    if new:
        send(new)


def main() -> int:
    today = datetime.now(PT).date()
    daily_by_metric = health.fetch_daily_by_metric()
    bella_section = bella.build_section(today)  # also refreshes Bella's history
    text = build_digest(today, daily_by_metric=daily_by_metric,
                        bella_section=bella_section)
    dry = bool(os.environ.get("DRY_RUN"))
    if not dry and os.environ.get("SLACK_BOT_TOKEN") and os.environ.get("SLACK_CHANNEL"):
        _send_slack(text)
        print("morning digest posted to Slack.")
    else:
        print(text)
    bella_series = bella.load_history(bella.DEFAULT_HISTORY)
    if dry:
        found = alerts.detect_alerts(daily_by_metric, bella_series, today)
        print(f"[dry-run] alerts that would be considered: {found or 'none'}")
    else:
        run_alerts(daily_by_metric, bella_series, today)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
