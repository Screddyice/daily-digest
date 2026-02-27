#!/usr/bin/env python3
"""
Daily Digest Health Check
Runs daily. Auto-fixes what it can. Only notifies Slack if something was fixed or is broken.
"""

import os
import json
import subprocess
import urllib.request
import urllib.parse
from datetime import datetime, timezone

MATON_BASE = "https://gateway.maton.ai"
MEETING_NOTES_FOLDER = "1kQ2qRpeYcr9ZFboPGM3sz_4vQBNvVZu8"
SLACK_CHANNEL = "C09M7UT9EJE"  # #trc-ops
LOG_FILE = "/home/ubuntu/logs/daily-digest-healthcheck.log"
SCRIPT_DIR = "/home/ubuntu/.openclaw/skills/daily-digest"
CRON_PATTERN = "daily-digest"
CRON_LINE = "0 6 * * * . /home/ubuntu/.openclaw/.env && cd /home/ubuntu/.openclaw/skills/daily-digest && /usr/bin/python3 run.py >> /home/ubuntu/logs/daily-digest.log 2>&1"

MATON_API_KEY = os.environ.get("MATON_API_KEY", "")
SLACK_BOT_TOKEN = os.environ.get("SLACK_BOT_TOKEN", "")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")

fixes = []
errors = []


def log(msg):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line)
    try:
        os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
        with open(LOG_FILE, "a") as f:
            f.write(line + "\n")
    except:
        pass


def api_get(url, headers):
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read().decode())


def api_post(url, headers, data):
    req = urllib.request.Request(url, data=json.dumps(data).encode(), method="POST", headers=headers)
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read().decode())


def check_cron():
    result = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
    if CRON_PATTERN in result.stdout and "run.py" in result.stdout:
        log("OK: cron entry exists")
        return
    existing = result.stdout.strip()
    new_cron = f"{existing}\n{CRON_LINE}\n" if existing else f"{CRON_LINE}\n"
    subprocess.run(["crontab", "-"], input=new_cron, text=True)
    fixes.append("Re-added missing daily-digest cron entry")
    log("FIXED: re-added cron entry")


def check_run_py():
    run_path = os.path.join(SCRIPT_DIR, "run.py")
    if not os.path.exists(run_path):
        errors.append("run.py is missing!")
        log("ERROR: run.py missing")
        return
    result = subprocess.run(
        ["python3", "-c", f"import py_compile; py_compile.compile('{run_path}', doraise=True)"],
        capture_output=True, text=True
    )
    if result.returncode == 0:
        log("OK: run.py syntax valid")
    else:
        errors.append(f"run.py has syntax errors: {result.stderr[:200]}")
        log(f"ERROR: run.py syntax: {result.stderr[:200]}")


def check_log_dir():
    log_dir = "/home/ubuntu/logs"
    if not os.path.isdir(log_dir):
        os.makedirs(log_dir, exist_ok=True)
        fixes.append("Created missing logs directory")
        log("FIXED: created logs directory")
    else:
        log("OK: logs directory exists")


def check_last_run():
    """Check if the digest ran recently (within last 36 hours)."""
    log_path = "/home/ubuntu/logs/daily-digest.log"
    if not os.path.exists(log_path):
        errors.append("daily-digest.log doesn't exist — script may have never run")
        log("ERROR: no run log found")
        return
    mtime = os.path.getmtime(log_path)
    hours_ago = (datetime.now().timestamp() - mtime) / 3600
    if hours_ago > 36:
        errors.append(f"Daily digest last ran {hours_ago:.0f}h ago — may be stalled")
        log(f"WARN: last run was {hours_ago:.0f}h ago")
    else:
        log(f"OK: last run {hours_ago:.1f}h ago")


def check_maton():
    if not MATON_API_KEY:
        errors.append("MATON_API_KEY not set in .env")
        log("ERROR: MATON_API_KEY not set")
        return
    try:
        api_get("https://ctrl.maton.ai/connections?status=ACTIVE",
                {"Authorization": f"Bearer {MATON_API_KEY}"})
        log("OK: Maton API key valid")
    except Exception as e:
        errors.append(f"Maton API key invalid or expired: {e}")
        log(f"ERROR: Maton API: {e}")


def check_calendar():
    try:
        now = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
        url = f"{MATON_BASE}/google-calendar/calendar/v3/calendars/primary/events?timeMin={now}&maxResults=1"
        api_get(url, {"Authorization": f"Bearer {MATON_API_KEY}"})
        log("OK: Google Calendar accessible")
    except Exception as e:
        errors.append(f"Google Calendar API failed: {e}")
        log(f"ERROR: Calendar: {e}")


def check_gmail():
    try:
        url = f"{MATON_BASE}/google-mail/gmail/v1/users/me/messages?maxResults=1"
        api_get(url, {"Authorization": f"Bearer {MATON_API_KEY}"})
        log("OK: Gmail accessible")
    except Exception as e:
        errors.append(f"Gmail API failed: {e}")
        log(f"ERROR: Gmail: {e}")


def check_linear():
    try:
        api_post(
            "https://gateway.maton.ai/linear/graphql",
            {"Authorization": f"Bearer {MATON_API_KEY}", "Content-Type": "application/json"},
            {"query": "{ viewer { id } }"}
        )
        log("OK: Linear accessible")
    except Exception as e:
        errors.append(f"Linear API failed: {e}")
        log(f"ERROR: Linear: {e}")


def check_drive_folder():
    try:
        q = urllib.parse.quote(f"'{MEETING_NOTES_FOLDER}' in parents")
        url = f"{MATON_BASE}/google-drive/drive/v3/files?q={q}&pageSize=1"
        api_get(url, {"Authorization": f"Bearer {MATON_API_KEY}"})
        log("OK: Drive Meeting Notes folder accessible")
    except Exception as e:
        errors.append(f"Drive Meeting Notes folder inaccessible: {e}")
        log(f"ERROR: Drive folder: {e}")


def check_slack():
    if not SLACK_BOT_TOKEN:
        errors.append("SLACK_BOT_TOKEN not set in .env")
        log("ERROR: SLACK_BOT_TOKEN not set")
        return
    try:
        resp = api_post(
            "https://slack.com/api/auth.test",
            {"Authorization": f"Bearer {SLACK_BOT_TOKEN}", "Content-Type": "application/json"},
            {}
        )
        if resp.get("ok"):
            log("OK: Slack bot token valid")
        else:
            errors.append(f"Slack bot token invalid: {resp.get('error')}")
            log(f"ERROR: Slack: {resp.get('error')}")
    except Exception as e:
        errors.append(f"Slack API failed: {e}")
        log(f"ERROR: Slack: {e}")


def check_anthropic():
    if not ANTHROPIC_API_KEY:
        errors.append("ANTHROPIC_API_KEY not set in .env")
        log("ERROR: ANTHROPIC_API_KEY not set")
        return
    try:
        req = urllib.request.Request(
            "https://api.anthropic.com/v1/messages",
            data=json.dumps({"model": "claude-sonnet-4-5-20250929", "max_tokens": 1, "messages": [{"role": "user", "content": "hi"}]}).encode(),
            method="POST",
            headers={"x-api-key": ANTHROPIC_API_KEY, "anthropic-version": "2023-06-01", "Content-Type": "application/json"}
        )
        urllib.request.urlopen(req, timeout=15)
        log("OK: Anthropic API key valid")
    except urllib.error.HTTPError as e:
        if e.code == 401:
            errors.append("Anthropic API key invalid/expired")
            log("ERROR: Anthropic key invalid")
        else:
            log("OK: Anthropic API key valid (non-auth error ignored)")
    except Exception as e:
        errors.append(f"Anthropic API unreachable: {e}")
        log(f"ERROR: Anthropic: {e}")


def notify_slack(message):
    if not SLACK_BOT_TOKEN:
        return
    try:
        api_post(
            "https://slack.com/api/chat.postMessage",
            {"Authorization": f"Bearer {SLACK_BOT_TOKEN}", "Content-Type": "application/json"},
            {"channel": SLACK_CHANNEL, "text": message, "mrkdwn": True}
        )
    except:
        pass


def main():
    log("=" * 50)
    log("Daily Digest Health Check starting")

    check_log_dir()
    check_cron()
    check_run_py()
    check_last_run()
    check_maton()
    if MATON_API_KEY:
        check_calendar()
        check_gmail()
        check_linear()
        check_drive_folder()
    check_slack()
    check_anthropic()

    log(f"Result: {len(fixes)} fixes, {len(errors)} errors")
    log("=" * 50)

    if fixes or errors:
        parts = []
        if fixes:
            parts.append("*Daily Digest -- auto-fixed:*\n" + "\n".join(f"• {f}" for f in fixes))
        if errors:
            parts.append("*Daily Digest -- needs attention:*\n" + "\n".join(f"• {e}" for e in errors))
        notify_slack("\n\n".join(parts))


if __name__ == "__main__":
    main()
