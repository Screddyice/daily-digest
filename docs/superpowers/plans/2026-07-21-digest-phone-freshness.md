# Digest Phone-Freshness Guard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the morning digest's health section degrade honestly when the iPhone stops pushing to HAE, instead of rendering week-old activity as current.

**Architecture:** Pure-render change in `health.py` mirroring the existing wrist-gap pattern: compute a `phone_gap` from the activity metrics and branch the activity block into fresh / stale / empty states. Ops tail: commit the box's uncommitted Telegram-delivery drift on a stacked branch, deploy `health.py` only, disable the dead `hae-tunnel.service`.

**Tech Stack:** Python stdlib only (repo rule), stdlib `unittest`.

## Global Constraints

- Stdlib only — no new dependencies (repo CLAUDE.md).
- Fresh-path rendering must stay byte-identical to current output.
- `morning.py` must NOT be deployed from this branch (box runs unmerged `bug-fix/bella-fi-normalization` + Telegram drift).
- Never commit to `main`; Conventional Commits; every branch gets a PR.
- Spec: `docs/superpowers/specs/2026-07-21-digest-phone-freshness-design.md`.
- Refinement over spec example: the stale "Last activity" line includes only metrics whose own latest data day equals the overall last data day (avoids stamping June values with a July date).

---

### Task 1: Failing tests for the phone-freshness states

**Files:**
- Modify: `test_health.py` (append a new case class after `WatchStateTests`)

**Interfaces:**
- Consumes: `health.render_section(daily_by_metric, today)` (existing), `_series` helper + `TODAY = date(2026, 5, 27)` (existing in test file).
- Produces: test names Task 2 must make pass: `PhoneStateTests.{test_fresh_phone_renders_comparisons_no_stale_line, test_stale_phone_shows_gap_line_and_suppresses_comparisons, test_gap_two_days_is_fresh, test_gap_three_days_is_stale, test_no_activity_data_shows_thirty_day_message, test_stale_phone_and_stale_wrist_show_both_lines, test_stale_last_known_line_skips_older_metrics}`.

- [ ] **Step 1: Append the failing tests**

```python
class PhoneStateTests(unittest.TestCase):
    """Freshness guard for iPhone-sourced activity metrics."""

    @staticmethod
    def _activity(end: date) -> dict:
        return {
            "step_count": _series(end, 8, 3138),
            "active_energy": _series(end, 8, 86),
            "apple_exercise_time": _series(end, 8, 2),
        }

    def test_fresh_phone_renders_comparisons_no_stale_line(self):
        out = health.render_section(self._activity(TODAY), TODAY)
        self.assertIn("vs yesterday", out)
        self.assertIn("*Activity", out)
        self.assertNotIn("📵", out)

    def test_stale_phone_shows_gap_line_and_suppresses_comparisons(self):
        out = health.render_section(self._activity(TODAY - timedelta(days=8)), TODAY)
        self.assertIn("📵 No phone health data for 8 days (last data May 19)", out)
        self.assertIn("Last activity (May 19): 3,138 steps · 86 kcal · 2 min exercise", out)
        self.assertNotIn("vs yesterday", out)
        self.assertNotIn("*Activity", out)
        self.assertNotIn("average day", out)

    def test_gap_two_days_is_fresh(self):
        out = health.render_section(self._activity(TODAY - timedelta(days=2)), TODAY)
        self.assertNotIn("📵", out)
        self.assertIn("*Activity", out)

    def test_gap_three_days_is_stale(self):
        out = health.render_section(self._activity(TODAY - timedelta(days=3)), TODAY)
        self.assertIn("📵 No phone health data for 3 days", out)

    def test_no_activity_data_shows_thirty_day_message(self):
        data = {"heart_rate_variability": _series(TODAY - timedelta(days=20), 8, 45)}
        out = health.render_section(data, TODAY)
        self.assertIn("📵 No phone health data in the last 30 days — check Health Auto Export.", out)

    def test_stale_phone_and_stale_wrist_show_both_lines(self):
        data = self._activity(TODAY - timedelta(days=8))
        data["heart_rate_variability"] = _series(TODAY - timedelta(days=34), 8, 38)
        out = health.render_section(data, TODAY)
        self.assertIn("📵 No phone health data for 8 days", out)
        self.assertIn("⌚ Watch off 34 days", out)

    def test_stale_last_known_line_skips_older_metrics(self):
        data = {
            "step_count": _series(TODAY - timedelta(days=8), 8, 3138),
            "active_energy": _series(TODAY - timedelta(days=20), 8, 86),
        }
        out = health.render_section(data, TODAY)
        self.assertIn("Last activity (May 19): 3,138 steps", out)
        self.assertNotIn("kcal", out)
```

- [ ] **Step 2: Run the new tests to verify they fail**

Run: `python3 -m unittest test_health.PhoneStateTests -v`
Expected: FAIL — stale/empty/boundary cases fail (no 📵 branch exists yet); fresh case passes.

### Task 2: Implement the guard in `health.py`

**Files:**
- Modify: `health.py` (constant near line 28; activity block inside `render_section`, currently lines 249–276; module + function docstrings)

**Interfaces:**
- Consumes: `_days_since_last`, `_recent_avg`, `_activity_label`, `_value_n_days_ago`, `_signed_pct`, `ACTIVITY_METRICS`, `LOOKBACK_DAYS` (all existing).
- Produces: `PHONE_FRESH_DAYS = 2` module constant; unchanged public signatures.

- [ ] **Step 1: Add the constant under `WRIST_FRESH_DAYS`**

```python
WRIST_FRESH_DAYS = 2        # watch metrics older than this read as "off-wrist"
PHONE_FRESH_DAYS = 2        # activity metrics older than this read as "phone not pushing"
```

- [ ] **Step 2: Replace the activity block in `render_section`**

Replace everything from `# ---------- Activity (iPhone-sourced; always shown if any data) ----------` through the `L.append("")` that closes the activity block with:

```python
    # ---------- Activity (iPhone-sourced; freshness-guarded) ----------
    steps = daily_by_metric.get("step_count", {})
    phone_gaps = [
        g for g in (
            _days_since_last(daily_by_metric.get(m, {}), today)
            for m, _how, _label, _fmt in ACTIVITY_METRICS
        )
        if g is not None
    ]
    phone_gap = min(phone_gaps) if phone_gaps else None

    if phone_gap is None:
        L.append(f"📵 No phone health data in the last {LOOKBACK_DAYS} days — check Health Auto Export.")
        L.append("")
    elif phone_gap > PHONE_FRESH_DAYS:
        last_date = today - timedelta(days=phone_gap)
        last_iso = last_date.isoformat()
        parts = [
            f"{fmt.format(d[max(d)])} {label}"
            for metric, _how, label, fmt in ACTIVITY_METRICS
            if (d := daily_by_metric.get(metric, {})) and max(d) == last_iso
        ]
        L.append(
            f"📵 No phone health data for {phone_gap} days (last data {last_date:%b %-d}) — "
            f"open Health Auto Export on the iPhone and re-run its automation."
        )
        if parts:
            L.append(f"   Last activity ({last_date:%b %-d}): " + " · ".join(parts))
        L.append("")
    else:
        activity_day = max(steps) if steps else None
        parts = []
        for metric, _how, label, fmt in ACTIVITY_METRICS:
            d = daily_by_metric.get(metric, {})
            if d:
                parts.append(f"{fmt.format(d[max(d)])} {label}")
        if parts:
            day_lbl = f" ({date.fromisoformat(activity_day):%a})" if activity_day else ""
            today_steps = steps[max(steps)] if steps else 0
            avg7 = _recent_avg(steps, 7) or 0
            label = _activity_label(today_steps, avg7) if avg7 else ""
            header = f"*Activity{day_lbl}:* " + " · ".join(parts)
            if label:
                header += f"  _{label}_"
            L.append(header)
            if steps:
                comps: list[str] = []
                y = _value_n_days_ago(steps, today, 1)
                w = _value_n_days_ago(steps, today, 7)
                if y is not None:
                    comps.append(f"vs yesterday {y:,.0f} ({_signed_pct(today_steps - y, y)})")
                if w is not None:
                    comps.append(f"vs week ago {w:,.0f} ({_signed_pct(today_steps - w, w)})")
                if comps:
                    L.append("   " + "  ·  ".join(comps))
            L.append("")
```

- [ ] **Step 3: Update the two docstrings**

Module docstring, replace the sentence claiming iPhone metrics are "always fresh" with: activity is freshness-guarded — when the phone stops pushing, the section says so explicitly (`📵` line) instead of rendering stale numbers. Add one sentence to `render_section`'s docstring naming the three activity states (fresh / stale / empty).

- [ ] **Step 4: Run the new tests, then the whole suite**

Run: `python3 -m unittest test_health -v` → all pass.
Run: `python3 -m unittest discover -p 'test_*.py'` → 184 + 7 tests pass, 0 failures.

- [ ] **Step 5: Commit**

```bash
git add health.py test_health.py
git commit -m "fix(digest): guard health section against stale phone data

When Health Auto Export stops pushing (no activity data for >2 days),
say so explicitly with the last-data date instead of rendering week-old
steps as today with +0% self-comparisons. Mirrors the wrist-gap pattern.

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

### Task 3: Push branch + open PR

**Files:** none (git/GitHub only)

- [ ] **Step 1:** `git push -u origin bug-fix/digest-phone-freshness`
- [ ] **Step 2:** Open PR base `main`, title `fix(digest): guard health section against stale phone data`; body summarizes the two states + spec path; ends with `🤖 Generated with [Claude Code](https://claude.com/claude-code)`. If the auto-pr-push hook already opened a draft, mark it ready and edit instead of creating a second PR.
Expected: PR URL printed; exactly one open PR for the branch.

### Task 4: Reconcile the box's Telegram-delivery drift (stacked branch)

**Files:**
- Modify: `morning.py` (on branch `feat/be-digest-telegram-delivery`, stacked on `bug-fix/bella-fi-normalization`)
- Modify: `test_morning.py` (delivery-routing test)

**Interfaces:**
- Produces: `_send_telegram(text, *, run=subprocess.run)` in `morning.py`; `DIGEST_DELIVERY` env switch (`telegram` default, `slack` opt-in).

- [ ] **Step 1:** `git switch -c feat/be-digest-telegram-delivery bug-fix/bella-fi-normalization`
- [ ] **Step 2:** Apply the box's diff to `morning.py` exactly (scratchpad copy `box_morning.py` is the source of truth): add `import subprocess`, `from pathlib import Path`, the `_send_telegram` function, and the `DIGEST_DELIVERY` routing in `main` (`telegram` default → `_send_telegram(text)`, `slack` → existing Slack post). After editing, `diff morning.py <scratchpad>/box_morning.py` must report no differences.
- [ ] **Step 3:** Add a routing test to `test_morning.py` following that file's existing mock style: `DIGEST_DELIVERY=telegram` (default env) calls `_send_telegram`'s `run` with `hermes send -t telegram`; `DIGEST_DELIVERY=slack` + Slack env posts to Slack and does not invoke hermes.
- [ ] **Step 4:** `python3 -m unittest discover -p 'test_*.py'` → all pass.
- [ ] **Step 5:** Commit `feat(digest): route delivery via Hermes Telegram by default` (note in body: reconciles code already live on neb-brain-hostinger since 2026-07-21; `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`), push, PR with base `bug-fix/bella-fi-normalization` noting it retargets to main once PR #23 merges.

### Task 5: Deploy + verify + tunnel cleanup

**Files:** remote `hostinger:~/digests/morning/health.py` only.

- [ ] **Step 1:** From the `bug-fix/digest-phone-freshness` branch: `scp health.py hostinger:~/digests/morning/health.py`
- [ ] **Step 2:** Live render on the box: `ssh hostinger 'cd ~/digests/morning && set -a && . ./.env && set +a && python3 health.py'`
Expected: section shows `📵 No phone health data for 8 days (last data Jul 13) …` + `Last activity (Jul 13): 3,138 steps` + the existing `⌚ Watch off 34 days` line. No `*Activity…(Mon)`, no `+0%`.
- [ ] **Step 3:** Remote unittest sanity: `ssh hostinger 'cd ~/digests/morning && python3 -m unittest test_health -v'` → all pass (box has the repo's test files; if `test_health.py` is absent there, scp it too, rerun).
- [ ] **Step 4:** `ssh hostinger 'systemctl --user disable --now hae-tunnel.service && systemctl --user is-enabled hae-tunnel.service; systemctl --user is-active hae-tunnel.service'`
Expected: `disabled` / `inactive` (or `failed→inactive`); no more 10s crash loop in `journalctl --user -u hae-tunnel.service -f`.
- [ ] **Step 5:** Confirm the timer will use the new code tomorrow: `ssh hostinger 'systemctl --user list-timers | grep morning-digest'` (unchanged schedule, next run 07:00 UTC).
