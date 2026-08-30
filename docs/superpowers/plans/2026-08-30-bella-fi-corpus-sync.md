# Bella Fi to Corpus Sync Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fetch Bella's current Fi health signals on the cloud host and store validated, subject-isolated rows in Corpus.

**Architecture:** Refactor the existing Fi collector so one structured snapshot feeds both the existing morning renderer and a new Corpus writer. A small CLI runs the sync with `KnowledgeClient.from_env()` and supports a write-free dry run for deployment verification.

**Tech Stack:** Python 3 standard library, `unittest`, Fi GraphQL, `shawn_corpus.KnowledgeClient`, PostgreSQL `health_metrics`.

**Spec:** `docs/2026-08-30-combined-shawn-bella-health-design.md`

## Global Constraints

- Run on `screddy-consult` as `hermes`; do not depend on Shawn's Mac.
- Store `source="fi"`, `bella_` metric names, and `raw.subject="Bella"`.
- Never write a missing Fi field as zero.
- Keep Fi credentials and full responses out of logs and Corpus `raw`.
- Preserve the current Bella morning section and local or gist-backed history.
- Confirm a live Fi value before adding any field beyond the captured fixtures.

---

### Task 1: Structured Bella snapshot

**Files:**
- Modify: `bella.py`
- Test: `test_bella.py`

**Interfaces:**
- Produces: `FiSyncError(RuntimeError)` for connector failures.
- Produces: `BellaSnapshot(pet_id: str, pet_name: str, synced_at: str, series: dict[str, dict[str, float]], directions: dict[str, str])`.
- Produces: `collect_snapshot(today: date, *, env: dict | None = None, history_path: Path = DEFAULT_HISTORY, profile_path: Path = DEFAULT_PROFILE, gql=None, pet_name: str | None = None) -> BellaSnapshot`.
- Preserves: `build_section(...) -> str | None` by rendering a successful snapshot and returning `None` when collection raises `FiSyncError`.

- [ ] **Step 1: Write the failing snapshot test**

Add a test that injects all five existing Fi fixtures and asserts:

```python
snapshot = bella.collect_snapshot(
    TODAY,
    env={"FI_EMAIL": "x", "FI_PASSWORD": "y"},
    history_path=history_path,
    profile_path=profile_path,
    gql=fake_gql,
)
assert snapshot.pet_id == "pet-123"
assert snapshot.pet_name == "Bella"
assert snapshot.series["steps"][TODAY.isoformat()] == 8421.0
assert snapshot.series["eating_events"][TODAY.isoformat()] == 3.0
assert snapshot.series["sleep"]["2026-06-11"] == 500.0
```

The production change this catches is a collector that renders text but fails
to expose the exact dated values needed by Corpus.

- [ ] **Step 2: Run the focused test and verify RED**

Run: `python3 -m unittest test_bella.StructuredSnapshotTests -v`

Expected: `AttributeError: module 'bella' has no attribute 'collect_snapshot'`.

- [ ] **Step 3: Implement the minimal snapshot API**

Add the dataclass and move the current transport, history merge, and direction
collection from `build_section` into `collect_snapshot`. Raise `FiSyncError`
for missing credentials, rejected login, missing Bella pet ID, or a snapshot
with no dated values. Keep behavior-trend and profile failures nonfatal when
steps or rest remain usable.

- [ ] **Step 4: Make the existing renderer consume the snapshot**

`build_section` catches `FiSyncError`, returns `None`, checks freshness and
readable signal, and calls `trends.render_pet_section` with
`snapshot.series` and `snapshot.directions`.

- [ ] **Step 5: Run snapshot and regression tests**

Run: `python3 -m unittest test_bella -v`

Expected: all Bella tests pass, including the new snapshot test.

- [ ] **Step 6: Commit**

```bash
git add bella.py test_bella.py
git commit -m "refactor: expose structured Bella Fi snapshot"
```

### Task 2: Validated Corpus writer

**Files:**
- Create: `bella_corpus.py`
- Create: `test_bella_corpus.py`

**Interfaces:**
- Consumes: `BellaSnapshot` from Task 1.
- Produces: `CorpusRow(metric: str, value: float, unit: str, day: str, fi_field: str)`.
- Produces: `snapshot_rows(snapshot: BellaSnapshot) -> list[CorpusRow]`.
- Produces: `write_snapshot(snapshot: BellaSnapshot, client: object) -> int`.

- [ ] **Step 1: Write failing mapping tests**

Create a literal `BellaSnapshot` with steps, sleep, rest, and behavior series.
Assert the output contains these exact rows:

```python
assert ("bella_steps", 8421.0, "count", "2026-06-12") in compact
assert ("bella_sleep_minutes", 500.0, "min", "2026-06-11") in compact
assert ("bella_eating_events", 3.0, "count", "2026-06-12") in compact
assert ("bella_licking_minutes", 1.0, "min", "2026-06-12") in compact
```

Include an unknown series key and assert that it produces no row. These tests
catch a wrong unit, missing prefix, or unreviewed Fi field reaching Corpus.

- [ ] **Step 2: Run the mapping tests and verify RED**

Run: `python3 -m unittest test_bella_corpus -v`

Expected: import failure because `bella_corpus.py` does not exist.

- [ ] **Step 3: Implement the allowlisted mapping**

Use one literal map from Fi history key to `(corpus metric, unit)`:

```python
{
    "steps": ("bella_steps", "count"),
    "sleep": ("bella_sleep_minutes", "min"),
    "activity_steps": ("bella_activity_steps", "count"),
    "rest_min": ("bella_rest_minutes", "min"),
    "barking_events": ("bella_barking_events", "count"),
    "eating_events": ("bella_eating_events", "count"),
    "eating_min": ("bella_eating_minutes", "min"),
    "drinking_events": ("bella_drinking_events", "count"),
    "licking_events": ("bella_licking_events", "count"),
    "licking_min": ("bella_licking_minutes", "min"),
    "scratching_events": ("bella_scratching_events", "count"),
}
```

Reject nonfinite values and values below zero.

- [ ] **Step 4: Write the failing write-contract test**

Use a recording client and assert every call includes:

```python
source="fi"
raw={
    "subject": "Bella",
    "pet_id": "pet-123",
    "synced_at": "2026-06-12T12:00:00+00:00",
    "fi_field": "steps",
}
```

Assert `write_snapshot` returns the number of calls. This catches a row that
can enter Shawn's namespace or lose Bella provenance.

- [ ] **Step 5: Implement and verify the writer**

Call `client.upsert_health_metric` once per `CorpusRow`, using its exact day.

Run: `python3 -m unittest test_bella_corpus -v`

Expected: all mapping and writer tests pass.

- [ ] **Step 6: Commit**

```bash
git add bella_corpus.py test_bella_corpus.py
git commit -m "feat: persist Bella Fi metrics in Corpus"
```

### Task 3: Cloud sync CLI and operating contract

**Files:**
- Create: `bella_sync.py`
- Create: `test_bella_sync.py`
- Modify: `.env.example`
- Modify: `README.md`
- Modify: `CLAUDE.md`

**Interfaces:**
- Consumes: `bella.collect_snapshot` and `bella_corpus.write_snapshot`.
- Produces: `main(argv: list[str] | None = None, *, collect=..., client_factory=...) -> int`.
- Exit `0`: live snapshot collected and written, or a dry run collected data.
- Exit `1`: connector, validation, or Corpus failure.
- `--dry-run`: print report date, pet name, metric names, and row count; perform no write.

- [ ] **Step 1: Write failing CLI behavior tests**

Test a dry run with an injected snapshot and a client factory that raises if
called. Assert exit `0`, no client construction, and output containing `Bella`,
the date, `bella_steps`, and the row count. Test a live run with a recording
client and assert it returns `0`. Test `FiSyncError` and assert exit `1` without
printing credentials or a fixture response.

- [ ] **Step 2: Run the CLI tests and verify RED**

Run: `python3 -m unittest test_bella_sync -v`

Expected: import failure because `bella_sync.py` does not exist.

- [ ] **Step 3: Implement the CLI**

Parse `--date`, `--dry-run`, and `--verbose`. Load the client lazily through:

```python
from shawn_corpus import KnowledgeClient
client = KnowledgeClient.from_env()
```

Print metric names from `snapshot_rows`; never print values or raw Fi payloads
in normal mode.

- [ ] **Step 4: Update deployment documentation**

Document these cloud checks:

```bash
~/shawn-corpus/.venv/bin/python3 bella_sync.py --dry-run
~/shawn-corpus/.venv/bin/python3 bella_sync.py
```

State that `FI_EMAIL`, `FI_PASSWORD`, and `RDS_URL` must exist in the service
environment and that the combined health relay invokes this CLI before reading
Bella's rows.

- [ ] **Step 5: Run the repository suite**

Run: `python3 -m unittest discover -p 'test_*.py'`

Expected: all tests pass.

- [ ] **Step 6: Commit**

```bash
git add bella_sync.py test_bella_sync.py .env.example README.md CLAUDE.md
git commit -m "feat: add cloud Bella health sync command"
```
