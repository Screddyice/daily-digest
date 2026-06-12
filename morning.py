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

import bella
import health
import trends

PT = ZoneInfo("America/Los_Angeles")


def build_digest(today=None, *, daily_by_metric=None, bella_section=None) -> str:
    today = today or datetime.now(PT).date()
    if daily_by_metric is None:
        daily_by_metric = health.fetch_daily_by_metric()
    if bella_section is None:
        bella_section = bella.build_section(today)
    return "\n".join([
        f"☀️  Morning Digest — {today:%A, %B %-d, %Y}",
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


def main() -> int:
    text = build_digest()
    if not os.environ.get("DRY_RUN") and os.environ.get("SLACK_BOT_TOKEN") and os.environ.get("SLACK_CHANNEL"):
        _send_slack(text)
        print("morning digest posted to Slack.")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
