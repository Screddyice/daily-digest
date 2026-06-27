# CLAUDE.md — project memory for daily-digest

Personal/work digests for Shawn (Team Nebula), delivered to Slack as the
**Nebula Assist** bot. Stdlib only, no dependencies. Tests are stdlib `unittest`.

## What ships where

Two digests, both posted to Slack DM **`D0AGFSC9PHN`** (Shawn ↔ Nebula Assist bot
`U0AFMJ70JV8`) via `morning._send_slack` (`SLACK_BOT_TOKEN` + `SLACK_CHANNEL`):

- **Morning Digest** — `morning.py`. Order: `🎯 Top 5 — NEBOS` (lead) → `💪 You`
  (health trends) → `🐕 Bella` (collar trends) → `📅 Meetings`.
- **Call Retro** — `retro.py`. End-of-day: re-synced `🎯 Top 5 — NEBOS` → `📞 Call`
  section (one-line Summary + one-line Action items parsed from the day's Gemini
  meeting notes).

## Modules

| file | role |
|---|---|
| `trends.py` | pure trend classification + rendering; `has_fresh_data()` |
| `health.py` | HAE fetch + legacy numeric renderer |
| `bella.py` | Fi collar (Bella) fetch + local step history; returns None when no new data |
| `nebos.py` | Top-5 work section: Linear issues + client Gmail, ranked |
| `meetings.py` | today's calendar; one combined context bullet per meeting |
| `retro.py` | Call Retro: Gemini-notes summary + action items |
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
`NEB_COMPOSIO_MCP_API_KEY` (+ `NEB_COMPOSIO_MCP_USER_ID`), `SLACK_BOT_TOKEN`
(Nebula Assist), `SLACK_CHANNEL=D0AGFSC9PHN`,
`NEBOS_CLIENT_DOMAINS=rivus.mx,newcalgon.net,rs21.io`, `NEBOS_RETRO_MEETING`
(optional — pin Call Retro to a meeting title; default = newest "Notes by Gemini"),
HAE (`HAE_BASE_URL`/`HAE_READ_TOKEN` or connector JSON), `FI_EMAIL`/`FI_PASSWORD`.
`DRY_RUN=1` prints instead of posting.

## Run / test
```bash
DRY_RUN=1 python3 morning.py
DRY_RUN=1 python3 retro.py
python3 -m unittest discover -p 'test_*.py'   # currently 149 tests, all passing
```

## Deployment = Claude routines (NOT this repo's CI)
The live **Daily Digest** and **Call Retro** are **Claude app routines**
(claude.ai). They are NOT editable from Claude Code / MCP — there is no routine
tool in this environment. To change delivery/behavior, edit the routine in the
Claude app (Settings → Routines) or a normal Claude chat. The recommended end
state: each routine `git pull`s this repo and runs `python3 morning.py` /
`python3 retro.py` so code is the single source of truth.

Note: the Composio Slack connection is a **user token** (shawnsreddy) — it CANNOT
post into `D0AGFSC9PHN` (returns `restricted_action_read_only_channel`); only the
Nebula Assist bot token can. Test previews from Claude Code land in the Composio
app DM (`D071FB7PRSA`) instead.

## History
- PR #7 (merged): NEBOS Top-5 section + client allowlist + drop-stale-sections.
- PR #8 (merged): Call Retro.

## GitHub
Session scope is `screddyice/daily-digest` only. Develop on
`claude/daily-digests-update-uhxe7o`; open PRs to `main`.
