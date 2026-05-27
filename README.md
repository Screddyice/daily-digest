# daily-digest

Shawn's personal **morning digest** — a watch-aware health readout, today's
meetings, and priorities, assembled from live sources. Stdlib only, no
dependencies.

## Sections

- **Health** (`health.py`) — pulls live from the self-hosted Health Auto Export
  (HAE) server every run. iPhone-sourced activity (steps / energy / exercise) is
  always fresh; Apple-Watch recovery metrics (HRV, resting HR, sleep) appear when
  the watch has been worn, and otherwise degrade to an explicit
  `⌚ Watch off N days` line instead of leaving the section blank.
- **Meetings** (`morning.py`) — today's events from the NEB Google Calendar
  (`shawn@teamnebula.ai`) via Composio.
- **Priorities** — TODO: wire from NEB Linear or the Jarvis brain.

## Run

```bash
python3 morning.py        # full digest
python3 health.py         # just the health section
```

## Environment

See `.env.example`. Designed to run on **neb-server**, where HAE is reachable
locally (the connector JSON is used as a fallback when `HAE_*` env vars are
unset) and the NEB Composio key is available.

## Tests

```bash
python3 -m unittest test_health -v
```
