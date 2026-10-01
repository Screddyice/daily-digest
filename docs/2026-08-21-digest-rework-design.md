# Daily-digest rework — corpus health source, Bella metrics, SRC-box deploy

**Date:** 2026-08-21  **Decided by:** Shawn  **Status:** design → implement after the health-connector plan lands

## Why
HAE was removed 2026-08-21, so `health.py` (which reads HAE `/api/metrics`) is dead. The new
personal-health pipeline is Apple Health → ChatGPT → health-connector MCP → corpus
`health_metrics` (on the SRC box). The digest was orphaned when the TMN box was decommissioned;
it now moves onto `screddy-consult` with everything else personal.

## Decisions (Shawn, 2026-08-21)
1. **Health section reads the corpus, not HAE.** Rewrite `health.py`'s fetch to read
   `health_metrics` rows directly via `shawn_corpus.KnowledgeClient` (the digest runs on the SRC
   box next to the DB). Keep `trends.py` rendering unchanged — new source, same trend lines.
   Graceful "no new data → drop the section" behavior is preserved (freshness check on `date`).
2. **Bella gets SEPARATE health metrics.** `bella.py` writes her Fi Series 3 data into
   `health_metrics` via `KnowledgeClient.upsert_health_metric(...)` with distinct metric names
   (`bella_steps`, `bella_sleep_hours`, ...) and `source="fi"`. No schema change — the separation
   is the metric-name namespace. The digest's Bella section then reads those rows the same way the
   human health section reads Shawn's, and Screddy can answer Bella questions from the brain.
   Local `~/.daily-digest/bella_history.json` step-accumulation stays (Fi only exposes the current
   daily total) but the persisted rows become the queryable record.
3. **Deploy on `screddy-consult` (SRC box), `hermes` user.** rsync the repo; two systemd user
   timers — `daily-digest-morning.timer` (~07:00) and `daily-digest-retro.timer` (end-of-day) —
   each running `python3 morning.py` / `retro.py`. Env from the fleet vault (SLACK_BOT_TOKEN,
   SLACK_CHANNEL, FI_EMAIL, FI_PASSWORD, NEB_COMPOSIO_MCP_API_KEY/NEBOS_MCP_*, plus the corpus DB
   creds the KnowledgeClient needs). Slack DM delivery unchanged.

## Tasks (implement after health-connector plan)
1. `health.py`: replace `load_hae_config`/`fetch_metric`/`aggregate_daily` HAE path with a
   `KnowledgeClient`-backed `fetch_metric_from_corpus(metric, days)` reading `health_metrics`;
   keep the aggregate→trend pipeline. Update `test_health.py` to the new source (fake client).
2. `bella.py`: after fetching Fi data, `upsert_health_metric("bella_steps"/"bella_sleep_hours",
   ..., source="fi")`; keep the local history file. Update `test_bella.py`.
3. `deploy/`: units + env.example + a deploy runbook; rsync + enable on the SRC box.
4. Verify end-to-end: `DRY_RUN=1 morning.py` on the box renders both health + Bella sections from
   corpus rows; timers active; one live post.

## Sequencing
Runs after the health-connector SDD plan (`shawn-corpus`) completes — the human-health section is
only meaningful once `health_metrics` is being fed. Bella's section is independent (Fi pull works
immediately). Depends on `KnowledgeClient.upsert_health_metric` (health-connector Task 1, merged).
