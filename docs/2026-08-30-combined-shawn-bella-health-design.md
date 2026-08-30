# Combined Shawn and Bella daily health delivery

**Date:** 2026-08-30  
**Status:** approved design, awaiting implementation review  
**Owners:** `Screddyice/daily-digest` and `Screddyice/shawn-corpus`

## Goal

Send one concise daily health report to Shawn by email and Hermes Telegram. The
report has two independent sections:

- `SHAWN`: Apple Health metrics supplied by the scheduled ChatGPT email.
- `BELLA`: health, activity, rest, and behavior signals supplied by Bella's Fi
  Series 3 collar.

Both subjects live in Corpus, but each keeps its own metric namespace, source,
history, trend calculations, and anomaly rules. No calculation may use one
subject's rows as another subject's baseline.

Telegram is Shawn's main reading surface. The combined email provides the same
report and remains available as a searchable record.

## Existing pieces

`shawn-corpus` already runs the cloud-hosted source flow:

1. Read the `Health Trend Review` source email through Composio Gmail.
2. Parse the machine-readable Apple Health payload.
3. Write Shawn's metrics to `health_metrics` with `source="apple_health"`.
4. Remove Gmail's `UNREAD` label after Corpus accepts the rows.
5. Relay the human report through Hermes Telegram with a retry ledger.

`daily-digest/bella.py` already authenticates with Fi, resolves Bella by name,
and reads steps, rest, sleep, and Series 3 behavior trends. It keeps local or
gist-backed history for Fi values that the API does not expose historically.
The implementation will reuse this collector and its fixtures.

## Chosen architecture

The production path runs on `screddy-consult` as the `hermes` user. Nothing in
this path depends on Shawn's Mac being online.

1. The scheduled ChatGPT task emails the Apple Health source report.
2. `health-email-ingest` validates the payload and writes Shawn's rows.
3. The job marks the source email read and verifies the label change.
4. The Bella collector performs a live Fi sync and writes Bella's rows.
5. The report builder reads both subjects from Corpus for the report date.
6. It creates one numeric, quick-read report with `SHAWN` and `BELLA` headings.
7. It sends the same report through Composio Gmail and Hermes Telegram.
8. A channel-aware ledger records each confirmed send, so retries send only the
   channel that remains incomplete.

The original ChatGPT email is an ingest source. Gmail cannot replace its body
after delivery, so the server sends a new combined email with a distinct
subject. The source email remains read. The combined email remains visible as
the final daily record.

## Repository responsibilities

| Repository | Responsibility |
|---|---|
| `daily-digest` | Authenticate with Fi, collect Bella's available signals, maintain Fi-only history, validate values, and upsert Bella rows into Corpus. |
| `shawn-corpus` | Ingest Shawn's source email, enforce subject isolation, query both subjects, calculate report trends and anomalies, and deliver the combined report. |

The relay reads Bella through Corpus. It does not import `daily-digest` modules
or call Fi itself. A command or service owned by `daily-digest` performs the Fi
sync before report composition. This keeps Fi transport code in one place and
gives the relay a stable database contract.

## Corpus contract

Shawn keeps the existing contract:

- `source="apple_health"`
- existing Apple Health metric names
- `raw.subject="Shawn"`
- `raw.via="chatgpt_email"`

Bella uses:

- `source="fi"`
- metric names prefixed with `bella_`
- `raw.subject="Bella"`
- `raw.pet_id`, `raw.synced_at`, and the Fi field used to produce the value
- the measurement date supplied by Fi, converted consistently before upsert

The first supported Bella metrics are:

| Corpus metric | Unit | Fi signal |
|---|---:|---|
| `bella_steps` | count | daily steps |
| `bella_sleep_minutes` | min | completed sleep and nap periods |
| `bella_activity_steps` | count | Fi Health activity summary, when distinct from daily steps |
| `bella_rest_minutes` | min | Fi Health rest duration |
| `bella_barking_events` | count | barking events |
| `bella_eating_events` | count | eating events |
| `bella_eating_minutes` | min | eating duration |
| `bella_drinking_events` | count | drinking events |
| `bella_licking_events` | count | licking events |
| `bella_licking_minutes` | min | licking duration |
| `bella_scratching_events` | count | scratching events |

The collector may add distance, walks, strain, sleep interruptions, or another
Fi Health field only after a live authenticated response and a captured test
fixture confirm its value and unit. The system will not infer these values from
prose or substitute a zero for an absent field.

The current unique key `(metric, UTC day, source)` remains sufficient because
the source and metric prefix isolate Bella from Shawn. No schema migration is
required for the first version.

## Trend and anomaly rules

The report builder performs all arithmetic. Hermes formats and transports the
finished text; it does not calculate trends.

Each subject uses only their own recent rows. The report shows current numbers,
plain-language movement against that subject's baseline, and meaningful
anomalies. It omits fields that Fi did not return.

Bella's first anomaly set reuses the deterministic, tested detectors in
`daily-digest/alerts.py`:

- sharp activity or step decline
- sharp increase or decrease in rest
- scratching or licking spike
- drinking spike
- eating decline

The text describes the observed change and suggests monitoring Bella. It does
not diagnose a condition. GPS location and collar battery stay out of the
health report. A separate operational alert may report a lost collar connection
or low battery if the Fi integration exposes a reliable signal later.

## Output contract

The report stays short enough to scan in Telegram:

```text
DAILY HEALTH - 2026-08-30

SHAWN
- Steps: 8,450, up 12% from your recent average
- Resting heart rate: 61 bpm, steady
- Flag: sleep is below your baseline for a third day

BELLA
- Steps: 10,220, down 18% from her recent average
- Rest: 12h 40m, above her baseline
- Flag: scratching rose sharply today
```

The renderer includes only observed metrics and supported comparisons. It does
not list missing sensors or measurements that Fi cannot provide.

Email subject:

```text
Daily Health: Shawn + Bella - YYYY-MM-DD
```

Hermes target:

```text
telegram:6056863584
```

The source Gmail query continues to match only `Health Trend Review`, so the
combined output email cannot re-enter the ingest path.

## Failure handling

- If the Apple Health payload is invalid, write nothing, leave the source email
  unread, and send no combined report.
- If Corpus rejects Shawn's rows, leave the source email unread.
- If Gmail cannot confirm the read transition, stop before delivery.
- If Fi fails, do not reuse stale Bella data as current. Send Shawn's section
  and emit one deduplicated operational alert for the Bella sync failure.
- If one output channel fails, keep the successful channel recorded and retry
  only the failed channel.
- When Fi recovers, clear the active failure state and send one recovery alert.

Fi errors never appear inside Bella's health section. The operational alert is
separate, so the health report remains a report of observed data.

## Delivery ledger

The ledger key includes the report date, source Gmail message ID, content hash,
and output channel. It records an email or Telegram send only after that channel
confirms success.

This supports these retry cases:

- Corpus and Gmail read succeed, email fails: retry email and Telegram as needed.
- Email succeeds, Telegram fails: retry Telegram only.
- The timer runs again after both sends: perform no external write.
- A corrected source email arrives for the same date: its message ID and content
  hash create a new report version without duplicating the prior version.

## Connector health

The Fi sync must prove four things on every run:

1. Authentication succeeds.
2. The account resolves the pet named `Bella`.
3. At least one dated health or activity value parses successfully.
4. Corpus confirms the upserted `source="fi"` rows for that date.

The service logs the pet ID, report date, parsed metric names, and row count. It
must not log Fi credentials or full responses. A state file edge-triggers one
Telegram alert on failure and one on recovery.

## Test plan

### `daily-digest`

- Keep network calls injectable and use captured, redacted Fi fixtures.
- Test each supported field and unit conversion.
- Test `source="fi"`, the `bella_` prefix, `raw.subject`, and idempotent reruns.
- Reject corrupt sleep durations, partial periods, missing values, and malformed
  behavior summaries.
- Prove that a Fi failure writes no stale replacement rows.

### `shawn-corpus`

- Prove Shawn and Bella queries cannot cross subject boundaries.
- Test numeric rendering, trend coverage, and each Bella anomaly.
- Test the exact order: Shawn upsert, Gmail read verification, Bella sync,
  report composition, email, Telegram.
- Test partial channel failures and ledger-driven retries.
- Test that the combined output subject cannot match the source Gmail query.
- Keep the existing malformed-source-email and duplicate-Telegram protections.

### Cloud verification

1. Run the Fi collector in dry-run mode on `screddy-consult` and inspect the
   parsed metric names without writing.
2. Run a real Fi sync and query the resulting `source="fi"` rows.
3. Run the combined relay against a known source email.
4. Confirm the source email is read, the combined email arrived, and one Hermes
   Telegram message arrived with both headings.
5. Run the same job again and confirm no duplicate email or Telegram message.

## Rollout

1. Merge and deploy the Fi-to-Corpus writer.
2. Verify Bella accumulates valid rows without changing delivery.
3. Merge the combined renderer and channel-aware ledger.
4. Deploy the combined email and Telegram path with the source email read rule.
5. Run one manual end-to-end test.
6. Keep the existing Shawn-only relay available for rollback until the combined
   path completes three successful daily runs.

Rollback disables the combined delivery and restores the current Shawn-only
relay. Bella's `source="fi"` rows can remain in Corpus because no Shawn query
includes that source.
