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
import subprocess
import urllib.request
from datetime import datetime
from pathlib import Path

import alerts
import bella
import health
import llm
import meetings
import nebos
import trends
import tzsafe

PT = tzsafe.resolve("America/Los_Angeles")
_UNSET = object()


def build_shawn_action_section(today, *, env=None, linear_call=None, meeting_call=None):
    """Combine Shawn-owned recent meeting actions with his assigned Linear work."""
    import retro  # retro imports morning for delivery; keep this dependency lazy
    env = os.environ if env is None else env
    meeting_items = retro.fetch_pending_items(today, env=env, call=meeting_call)
    return nebos.build_shawn_action_section(
        today, env=env, call=linear_call, meeting_items=meeting_items)


def build_digest(today=None, *, daily_by_metric=None, bella_section=_UNSET,
                 llm_body=_UNSET, meetings_section=None, nebos_section=_UNSET,
                 pending_section=_UNSET) -> str:
    today = today or datetime.now(PT).date()
    if daily_by_metric is None:
        daily_by_metric = health.fetch_daily_by_metric()
    if bella_section is _UNSET:
        bella_section = bella.build_section(today)
    if meetings_section is None:
        meetings_section = meetings.build_section(today)
    if nebos_section is _UNSET:
        nebos_section = build_shawn_action_section(today)
    if pending_section is _UNSET:
        # Recent meeting actions are merged into the Shawn-specific Top 5.
        pending_section = None

    header = f"☀️  Morning Digest — {today:%A, %B %-d, %Y}"
    blocks = [header]

    # Shawn-specific actions from recent meeting notes and his assigned Linear
    # tickets. The combined section is capped at five.
    if nebos_section:
        blocks += ["", nebos_section]

    # Still pending — open action items from the last few days of calls, Shawn's
    # first. Sits with the action sections up top; dropped when nothing is open.
    if pending_section:
        blocks += ["", pending_section]

    # You section — full trends only when the health feed has new data. A frozen
    # or absent feed never re-renders stale trends; instead a one-line 📵 note
    # says why health is missing and how to revive it (health.staleness_note).
    if trends.has_fresh_data(daily_by_metric, today):
        if llm_body is _UNSET:
            # Snapshot this export, compare against the previous one via the LLM.
            # The LLM writes ONLY the numberless "You" narrative; Bella's section
            # is rendered deterministically (in bella.build_section) so her real
            # numbers survive the digit-gate, which governs the You body alone.
            bella_series = bella.load_history(bella.DEFAULT_HISTORY)
            previous = llm.load_previous_snapshot(llm.SNAPSHOT_DIR, today)
            llm.save_snapshot(llm.SNAPSHOT_DIR, today, daily_by_metric, bella_series)
            current = {"date": today.isoformat(), "you": daily_by_metric}
            llm_body = llm.generate_digest(current, previous, today)
        you_block = llm_body or trends.render_you_section(daily_by_metric, today)
        if you_block:  # None when every metric is at baseline — drop, don't show a bare header
            blocks += ["", you_block]
    else:
        stale_note = health.staleness_note(daily_by_metric, today)
        if stale_note:
            blocks += ["", stale_note]

    # Bella section — build_section returns None when her collar has no new data,
    # in which case her section is dropped too.
    if bella_section:
        blocks += ["", bella_section]

    if meetings_section:  # always last; times are the one allowed digit zone
        blocks += ["", meetings_section]
    return "\n".join(blocks)


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


def _send_telegram(text: str, *, run=subprocess.run) -> None:
    """Send the digest to Shawn's Telegram DM via the Hermes gateway."""
    hermes = Path.home() / ".local" / "bin" / "hermes"
    r = run([str(hermes), "send", "-t", "telegram", "-s", "☀️ Morning Digest", text],
            capture_output=True, text=True, timeout=60)
    if r.returncode != 0:
        raise SystemExit(f"telegram post failed: {r.stderr[-200:]}")


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
    nebos_section = build_shawn_action_section(today)
    text = build_digest(today, daily_by_metric=daily_by_metric,
                        bella_section=bella_section, nebos_section=nebos_section)
    dry = bool(os.environ.get("DRY_RUN"))
    delivery = os.environ.get("DIGEST_DELIVERY", "telegram").lower()
    if not dry and delivery == "slack" and os.environ.get("SLACK_BOT_TOKEN") and os.environ.get("SLACK_CHANNEL"):
        _send_slack(text)
        print("morning digest posted to Slack.")
    elif not dry:
        _send_telegram(text)
        print("morning digest posted to Telegram.")
    else:
        print(text)
    bella_series = bella.load_history(bella.DEFAULT_HISTORY)
    if dry:
        found = alerts.detect_alerts(daily_by_metric, bella_series, today)
        print(f"[dry-run] alerts that would be considered: {found or 'none'}")
    elif os.environ.get("DIGEST_NO_ALERTS"):
        # Stateless runtime (e.g. a cloud routine in a fresh sandbox): skip the
        # edge-filtered Telegram alerts. They need a persistent STATE_PATH to
        # avoid re-alerting the same condition every run, which an ephemeral
        # sandbox can't provide. The digest itself has already posted above.
        pass
    else:
        run_alerts(daily_by_metric, bella_series, today)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
