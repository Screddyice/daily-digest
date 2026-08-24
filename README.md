# daily-digest

Shawn's morning digest — **trends only, never numbers** (the work section is the
one place real specifics are allowed, like meeting times).

One Slack DM, led by the day's priorities and followed by personal trends:

- **Shawn — Top 5 actions** 🎯 — up to five concrete things Shawn needs to send
  or do, merged from recent meeting notes that name him as owner and open Linear
  tickets assigned to his user. Each item shows its meeting or Linear source;
  Gmail, ownerless notes, and other teammates' tasks stay out.
- **You** — five plain-English lines from corpus `health_metrics` rows
  (Apple Health → ChatGPT scheduled Task → health-connector MCP → corpus):
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
| `health.py` | corpus health_metrics fetch + the legacy numeric renderer |
| `bella.py` | Fi login/GraphQL fetch + local step history |
| `nebos.py` | Shawn Top-5 ranking — assigned Linear + recent meeting actions |
| `meetings.py` | today's calendar via Composio |
| `morning.py` | morning digest composition + Slack delivery |
| `retro.py` | Call Retro — end-of-day NEBOS Top 5 + call summary/action items |

## Run

```bash
DRY_RUN=1 python3 morning.py     # print, don't post
python3 morning.py               # post to Slack when SLACK_BOT_TOKEN + SLACK_CHANNEL set

DRY_RUN=1 python3 retro.py       # Call Retro (end-of-day): NEBOS Top 5 + call recap
python3 retro.py                 # post the retro to Slack
```

**Call Retro** (`retro.py`) is the end-of-day counterpart: the re-synced NEBOS
Top 5, then a **Call** section with one line summarizing the day's call and one
line of its action items, parsed from the meeting's Gemini notes (`Summary` +
`Next steps`). It uses the newest "Notes by Gemini" doc; set
`NEBOS_RETRO_MEETING` to pin it to a specific recurring meeting by title.

Env: see `.env.example` (`RDS_URL` corpus DSN for the health section; `FI_EMAIL` /
`FI_PASSWORD` for Bella; `NEB_COMPOSIO_MCP_API_KEY` for the NEBOS + meetings
sections, with optional `NEBOS_COMPANY_DOMAIN`, default `teamnebula.ai`, to mark
which mail is internal).

## Tests

```bash
python3 -m unittest test_trends test_bella test_morning test_health test_nebos test_meetings -v
```

Stdlib only — no dependencies.

## NebOS MCP

Meeting store calls use NebOS **v2** MCP at
`https://nebos-api-960873997677.us-central1.run.app/api/mcp`
(`NEBOS_MCP_URL` / `NEBOS_MCP_TOKEN`). v1 Firebase hosting is retired for this client.
