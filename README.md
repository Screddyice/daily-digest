# daily-digest

Shawn's morning digest — **trends only, never numbers**.

Two sections, one Slack DM:

- **You** — five plain-English lines from live Health Auto Export (HAE) data:
  activity & exercise, lung health (blood oxygen), stress signals (HRV +
  resting HR), sleep sufficiency, and an illness watch that fires when
  recovery and oxygen move the wrong way together. Each line says
  increasing / decreasing / steady — raw values never appear.
- **Bella** 🐕 — the same treatment for the dog, from her Fi Series 3 collar.
  Sleep history comes from Fi's rest feed; step history accumulates locally
  (`~/.daily-digest/bella_history.json`) since Fi only exposes the current
  daily total. Trends become readable after about a week of runs.

If the data feed is frozen, the digest says so loudly
(`⚠️ … hasn't synced since Tuesday`) instead of silently re-rendering stale
numbers as fresh.

## Modules

| file | role |
|---|---|
| `trends.py` | pure trend classification + rendering (no I/O) |
| `health.py` | HAE fetch + the legacy numeric renderer |
| `bella.py` | Fi login/GraphQL fetch + local step history |
| `morning.py` | composition + Slack delivery |

## Run

```bash
DRY_RUN=1 python3 morning.py     # print, don't post
python3 morning.py               # post to Slack when SLACK_BOT_TOKEN + SLACK_CHANNEL set
```

Env: see `.env.example` (HAE base/token or connector JSON; `FI_EMAIL` /
`FI_PASSWORD` for Bella).

## Tests

```bash
python3 -m unittest test_trends test_bella test_morning test_health -v
```

Stdlib only — no dependencies.
