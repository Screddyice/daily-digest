#!/usr/bin/env python3
"""Call Retro runner for the Hostinger box (Hermes-composed).

Replaces the claude.ai cloud routine, which the cloud sandbox 403s on the repo
fetch ("GitHub access to this repository is not enabled for this session"). On
the box there is no such wall, and the LLM is local Hermes (codex OAuth, free).

Flow: deterministic gather via ``retro.py --json`` (crash-proof) -> Hermes writes
the message -> post to Slack. Degrades honestly at every step:
  - no/blank data          -> "data unavailable" one-liner (transient)
  - data present but empty  -> "No calls logged today."
  - Hermes unusable         -> retro.py's own deterministic render+post
Env (via the service EnvironmentFile): NEB_COMPOSIO_MCP_API_KEY(+_USER_ID),
NEBOS_MCP_TOKEN, NEBOS_CLIENT_DOMAINS, RETRO_TZ, SLACK_BOT_TOKEN, SLACK_CHANNEL,
DIGEST_NO_ALERTS=1. Optional: RETRO_HERMES_BIN, RETRO_HERMES_MODEL, RETRO_HERMES_TIMEOUT.
"""
import json
import os
import subprocess
import sys
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
HERMES_BIN = os.path.expanduser(os.environ.get("RETRO_HERMES_BIN", "~/.local/bin/hermes"))
HERMES_MODEL = os.environ.get("RETRO_HERMES_MODEL", "openai-codex/gpt-5.4")
HERMES_TIMEOUT = int(os.environ.get("RETRO_HERMES_TIMEOUT", "600"))
SLACK_TOKEN = os.environ.get("SLACK_BOT_TOKEN", "")
SLACK_CHANNEL = os.environ.get("SLACK_CHANNEL", "")

COMPOSE_PROMPT = """You are composing Shawn Reddy's end-of-day Call Retro, delivered to his Slack DM.
You are given DATA (JSON) with today's calls (from the NEBOS meeting store, which is Fireflies-fed)
and his Top-5 open work items. Turn it into a short, skimmable retro.

Rules:
- Slack mrkdwn. Bold section labels with *single asterisks*. No headers, no markdown tables, no em dashes.
- Put a BLANK LINE between every bullet and between sections so it breathes. Never a dense wall of text.
- One idea per line, kept short. Real substance only (names, numbers, the actual ask). Never invent; use ONLY the DATA.
- LEAD with action items, the whole point of the retro. Pull the pending to-dos from each call's next_steps.
  Put anything owed by Shawn first. One short line each. If nothing is genuinely owed, write exactly: Nothing on you today.
- Then the Top 5 (from top5), one short line each. Omit the whole section if top5 is empty.
- Then a Calls recap: one line per call, using its label_line plus a tight one-sentence takeaway from its summary.

Structure exactly:
🌙 Call Retro — <label>

*Action items*

- <pending to-do>  (Shawn's first; or "Nothing on you today.")

*Top 5 — NEBOS*   (omit this whole section if top5 is empty)

- <item>

*Calls*

- <label_line> — <one tight sentence>

Reply with ONLY the retro message, nothing else. Here is the DATA:
"""


def post(text):
    body = json.dumps({"channel": SLACK_CHANNEL, "text": text, "mrkdwn": True}).encode()
    req = urllib.request.Request(
        "https://slack.com/api/chat.postMessage", data=body,
        headers={"Authorization": "Bearer " + SLACK_TOKEN,
                 "Content-type": "application/json; charset=utf-8"})
    try:
        resp = json.loads(urllib.request.urlopen(req, timeout=30).read().decode())
    except Exception as exc:
        sys.stderr.write("slack post failed: %s\n" % exc)
        return False
    if not resp.get("ok"):
        sys.stderr.write("slack error: %s\n" % resp.get("error"))
    return bool(resp.get("ok"))


def gather():
    """Run retro.py --json (crash-proof) and return the parsed payload, or None."""
    try:
        p = subprocess.run([sys.executable, "retro.py", "--json"], cwd=HERE,
                           capture_output=True, text=True, timeout=180, env=os.environ)
    except Exception as exc:
        sys.stderr.write("gather crashed: %s\n" % exc)
        return None
    out = (p.stdout or "").strip()
    if p.returncode != 0 or not out:
        sys.stderr.write("gather rc=%s stderr=%s\n" % (p.returncode, (p.stderr or "")[-300:]))
        return None
    try:
        return json.loads(out.splitlines()[-1])
    except Exception as exc:
        sys.stderr.write("gather parse failed: %s\n" % exc)
        return None


def _strip_fences(out):
    out = (out or "").strip()
    if out.startswith("```"):
        lines = out.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        out = "\n".join(lines).strip()
    return out


def compose(data):
    """Hermes one-shot writes the message. None if the binary/model is unusable."""
    if not os.path.exists(HERMES_BIN):
        sys.stderr.write("hermes binary not found at %s\n" % HERMES_BIN)
        return None
    prompt = COMPOSE_PROMPT + json.dumps(data, ensure_ascii=False)
    env = dict(os.environ)
    env["HYPERSWARM_MEMORY_DISABLE"] = "1"  # keep one-shots out of Hermes working memory
    for attempt in (1, 2):
        try:
            p = subprocess.run([HERMES_BIN, "-m", HERMES_MODEL, "-z", prompt],
                               capture_output=True, text=True, timeout=HERMES_TIMEOUT, env=env)
        except subprocess.TimeoutExpired:
            sys.stderr.write("hermes attempt %d timed out\n" % attempt)
            continue
        msg = _strip_fences(p.stdout)
        if p.returncode == 0 and len(msg) > 40:
            sys.stderr.write("hermes ok attempt %d (%d chars)\n" % (attempt, len(msg)))
            return msg
        sys.stderr.write("hermes attempt %d unusable rc=%s stderr=%s\n"
                         % (attempt, p.returncode, (p.stderr or "")[-300:]))
    return None


def deterministic_render_and_post():
    """retro.py default mode renders AND posts itself when SLACK_* are set."""
    try:
        p = subprocess.run([sys.executable, "retro.py"], cwd=HERE,
                           capture_output=True, text=True, timeout=180, env=os.environ)
        return p.returncode == 0
    except Exception as exc:
        sys.stderr.write("deterministic fallback crashed: %s\n" % exc)
        return False


def main():
    if not (SLACK_TOKEN and SLACK_CHANNEL):
        sys.exit("ERROR: SLACK_BOT_TOKEN and SLACK_CHANNEL are required")

    data = gather()
    if data is None:
        post("🌙 Call Retro could not run tonight — data unavailable (transient). Will retry tomorrow.")
        return 0

    label = data.get("label") or ""
    calls = data.get("calls") or []
    top5 = data.get("top5") or []
    if not calls and not top5:
        post("🌙 Call Retro — %s\n\nNo calls logged today." % label)
        return 0

    msg = compose(data)
    if msg:
        post(msg)
        return 0

    # Hermes unusable -> deterministic render+post; last-resort honest one-liner.
    if not deterministic_render_and_post():
        post("🌙 Call Retro — %s\n\n%d call(s) today, but the write-up failed. Check the box logs."
             % (label, len(calls)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
