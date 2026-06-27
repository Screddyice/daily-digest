# daily-digest

Shawn's morning digest — **trends only, never numbers** (the work section is the
one place real specifics are allowed, like meeting times).

One Slack DM, led by the day's priorities and followed by personal trends:

- **Top 5 — NEBOS** 🎯 — the day's most important things to handle, pulled from
  the systems behind NEBOS (Team Nebula's central context hub): open Linear
  issues assigned to you (action items / to-dos) and client emails in Gmail
  still awaiting your reply. "Client" is an allowlist of domains
  (`NEBOS_CLIENT_DOMAINS`, default `rivus.mx,newcalgon.net,rs21.io`) so vendor
  and cold-outreach mail doesn't crowd the list. The two streams are merged,
  ranked by a simple urgency score (priority, overdue, and how long someone's
  been waiting), and the top five are shown — nothing else. Reached through the
  same Composio gateway as the meetings section.
- **You** — five plain-English lines from live Health Auto Export (HAE) data:
  activity & exercise, lung health (blood oxygen), stress signals (HRV +
  resting HR), sleep sufficiency, and an illness watch that fires when
  recovery and oxygen move the wrong way together. Each line says
  increasing / decreasing / steady — raw values never appear.
- **Bella** 🐕 — the same treatment for the dog, from her Fi Series 3 collar.
  Sleep history comes from Fi's rest feed; step history accumulates locally
  (`~/.daily-digest/bella_history.json`) since Fi only exposes the current
  daily total. Trends become readable after about a week of runs.

If a feed has no new data — frozen for a couple of days, or never configured —
that section is dropped from the digest entirely, rather than re-rendering stale
trends as if they were fresh. A section appears only when its feed synced today
or yesterday.

## Modules

| file | role |
|---|---|
| `trends.py` | pure trend classification + rendering (no I/O) |
| `health.py` | HAE fetch + the legacy numeric renderer |
| `bella.py` | Fi login/GraphQL fetch + local step history |
| `nebos.py` | Top-5 work section — Linear + Gmail via Composio |
| `meetings.py` | today's calendar via Composio |
| `morning.py` | composition + Slack delivery |

## Run

```bash
DRY_RUN=1 python3 morning.py     # print, don't post
python3 morning.py               # post to Slack when SLACK_BOT_TOKEN + SLACK_CHANNEL set
```

Env: see `.env.example` (HAE base/token or connector JSON; `FI_EMAIL` /
`FI_PASSWORD` for Bella; `NEB_COMPOSIO_MCP_API_KEY` for the NEBOS + meetings
sections, with optional `NEBOS_COMPANY_DOMAIN`, default `teamnebula.ai`, to mark
which mail is internal).

## Tests

```bash
python3 -m unittest test_trends test_bella test_morning test_health test_nebos test_meetings -v
```

Stdlib only — no dependencies.
