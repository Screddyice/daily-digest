# CLAUDE.md — project memory for daily-digest

Personal/work digests for Shawn (Team Nebula), delivered to Slack as the
**Nebula Assist** bot. Stdlib only, no dependencies. Tests are stdlib `unittest`.

## What ships where

Two digests, both posted to Slack DM **`D0AGFSC9PHN`** (Shawn ↔ Nebula Assist bot
`U0AFMJ70JV8`) via `morning._send_slack` (`SLACK_BOT_TOKEN` + `SLACK_CHANNEL`):

- **Morning Digest** — `morning.py`. Order: `🎯 Top 5 — NEBOS` (lead) →
  `📋 Still pending` (open action items carried forward from the last few days
  of calls, Shawn-owned first; from `retro.build_pending_section`) → `💪 You`
  (health trends) → `🐕 Bella` (collar trends) → `📅 Meetings`.
- **Call Retro** — `retro.py`. End-of-day: re-synced `🎯 Top 5 — NEBOS` → `📞 Calls`
  section recapping every call today, sourced from the **NEBOS meeting store**
  (the canonical Fireflies-fed record, via the `meeting_list` MCP tool). NEBOS is
  the source of truth — the retro reads it, not Fireflies/Gemini directly. Each
  call shows title, time, who it was with, a condensed summary, and a `Next:` line
  when the notes flag follow-ups.

## Modules

| file | role |
|---|---|
| `trends.py` | pure trend classification + rendering; `has_fresh_data()` |
| `health.py` | HAE fetch + legacy numeric renderer |
| `bella.py` | Fi collar (Bella) fetch + local step history; returns None when no new data |
| `nebos.py` | Top-5 work section: Linear issues + client Gmail, ranked |
| `meetings.py` | today's calendar; one combined context bullet per meeting |
| `retro.py` | Call Retro: NEBOS meeting-store (Fireflies) recap of today's calls |
| `morning.py` | morning digest composition + Slack delivery |

## NEBOS

NEBOS = Team Nebula's internal "central context hub" (a.k.a. Neb OS / Nebby). It
isn't a standalone API here — the Top-5 section "references" it by pulling the two
systems behind it via the **Composio REST gateway** (`NEB_COMPOSIO_MCP_API_KEY`,
same gateway `meetings.py` uses):

- **Linear** — open issues assigned to Shawn (action items / to-dos).
- **Gmail** — client emails awaiting a reply.

Ranking (tunable consts in `nebos.py`): Urgent/High and overdue Linear items first,
then recent client emails, capped at 5.

### Client allowlist (important)
Only mail from **client domains** counts as a "client email to reply to"
(`NEBOS_CLIENT_DOMAINS`, default below). Vendors/cold-outreach/calendar-RSVP and
internal `teamnebula.ai` mail are filtered out.

- Clients: **Rivus → `rivus.mx`**, **Newcalgon → `newcalgon.net`**, **RS21 → `rs21.io`**.
- OPEN: **Volttruckinc** domain unknown (no inbound mail found yet) — add when known.
- OPEN: **Ampere (`amperecomputing.com`)** is NOT in the client list — Craig
  Hardy's invoice thread is excluded unless Shawn says to add it.

## "No new data → drop the section"
The digest omits a section entirely when its feed has no new data (synced today or
yesterday; `trends.has_fresh_data` / `STALE_AFTER_DAYS`) instead of showing a stale
`⚠️ hasn't synced` warning. Applies to You (HAE) and Bella (Fi). `bella.build_section`
and `nebos.build_section` return `None` when unconfigured/unreachable/empty;
`morning.build_digest` uses `_UNSET` sentinels so `None` means *omit*, not *re-fetch*.

## Env vars
`NEB_COMPOSIO_MCP_API_KEY` (+ `NEB_COMPOSIO_MCP_USER_ID`) for the Top-5 section,
`NEBOS_MCP_TOKEN` (+ `NEBOS_MCP_URL`, default `https://teamnebula-os.web.app/api/mcp`)
for the Call Retro's meeting store, `SLACK_BOT_TOKEN` (Nebula Assist),
`SLACK_CHANNEL=D0AGFSC9PHN`, `NEBOS_CLIENT_DOMAINS=rivus.mx,newcalgon.net,rs21.io`,
`RETRO_TZ` (optional — timezone for the Call Retro's "today" window + header,
default `America/Los_Angeles`; set `Europe/London` etc. for where Shawn is based),
`PENDING_LOOKBACK_DAYS` (optional, default 3 — window for the Morning Digest's
`📋 Still pending` section; needs `NEBOS_MCP_TOKEN` like the Call Retro),
HAE (`HAE_BASE_URL`/`HAE_READ_TOKEN` or connector JSON), `FI_EMAIL`/`FI_PASSWORD`,
`BELLA_HISTORY_GIST` (optional — a secret gist id; with `GITHUB_TOKEN` it makes
Bella's step/behavior history round-trip through the gist so trends survive an
ephemeral sandbox that wipes the local `~/.daily-digest` file; sleep is unaffected,
served live by Fi).
`DRY_RUN=1` prints instead of posting. `DIGEST_NO_ALERTS=1` skips the stateful
Telegram alerts (the digest still posts) — set it in stateless runtimes like a
cloud routine, where the edge-filter STATE_PATH can't persist between runs.

When HAE is not configured at all (no env vars and no connector file, e.g. a
cloud sandbox), health degrades to empty and the `💪 You` section simply drops —
it never crashes the digest.

## Run / test
```bash
DRY_RUN=1 python3 morning.py
DRY_RUN=1 python3 retro.py
python3 -m unittest discover -p 'test_*.py'   # currently 184 tests, all passing
```

## Claude routine behavior
Do **not** send a `PushNotification` at the end of a morning digest run. The digest posts itself to Slack — that is the notification. Only notify via `PushNotification` if the script crashes or exits non-zero.

## Deployment = Claude routines (NOT this repo's CI)
The live **Daily Digest** and **Call Retro** are **Claude app routines**
(claude.ai). From a claude.ai cloud session there is no routine-editing tool, so
those sessions can't change delivery. From the **Mac CLI**, however, the
`RemoteTrigger` API tool manages these triggers (`list`/`get`/`update`/`run`
against `/v1/code/triggers`) — that's how the Call Retro trigger
(`trig_01GJrJVNK9LX6ntopDo5Y1cm`) is wired to clone this repo and run
`python3 retro.py`. End state: each routine `git pull`s this repo and runs
`python3 morning.py` / `python3 retro.py` so code is the single source of truth.

Note: the Composio Slack connection is a **user token** (shawnsreddy) — it CANNOT
post into `D0AGFSC9PHN` (returns `restricted_action_read_only_channel`); only the
Nebula Assist bot token can. Test previews from Claude Code land in the Composio
app DM (`D071FB7PRSA`) instead.

## History
- PR #7 (merged): NEBOS Top-5 section + client allowlist + drop-stale-sections.
- PR #8 (merged): Call Retro (originally Gemini-notes sourced).
- PR #9 (merged): this CLAUDE.md.
- PR #10: Call Retro re-sourced to the NEBOS meeting store (Fireflies); recaps
  every call today; `RETRO_TZ` window; `NEBOS_MCP_TOKEN`. Replaces the
  Gemini-Docs path.

## GitHub
Session scope is `screddyice/daily-digest` only. Develop on
`claude/daily-digests-update-uhxe7o`; open PRs to `main`.
