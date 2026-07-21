# Digest phone-freshness guard — design

Date: 2026-07-21
Status: approved (Shawn, via session Q&A)

## Problem

The morning digest's health section reads live from the self-hosted Health
Auto Export (HAE) server. When the iPhone stops pushing (as happened
2026-07-13), the section keeps rendering the last data day as if it were
current: a misleading day label ("(Mon)" for a day 8 days back), an
"average day" tag, and self-referential "+0% vs yesterday / vs week ago"
comparisons. The watch path already degrades honestly ("Watch off N days");
the phone path has no equivalent guard.

Out of scope (explicitly declined): a standing staleness alert, wiring the
shawn-corpus `today_digest` health section, changing health data sources.

## Design

All changes in `health.py` (pure render path) + `test_health.py`. Stdlib
only, matching the module's existing wrist-gap pattern.

1. New constant `PHONE_FRESH_DAYS = 2` beside `WRIST_FRESH_DAYS`.
2. In `render_section`, compute `phone_gap` = days between `today` and the
   freshest date across the activity metrics (`step_count`,
   `active_energy`, `apple_exercise_time`); `None` when all three are
   empty.
3. Render states for the activity block:
   - **Fresh** (`phone_gap <= PHONE_FRESH_DAYS`): unchanged from today's
     behavior (value line, activity label, comparison line).
   - **Stale** (`phone_gap > PHONE_FRESH_DAYS`): replace the whole activity
     block with:
     `📵 No phone health data for N days (last data Jul 13) — open Health
     Auto Export on the iPhone and re-run its automation.`
     plus one last-known line in the existing "Last HRV (Jun 17)" style:
     `   Last activity (Jul 13): 3,138 steps · 86 kcal · 2 min exercise`
     No day label, no activity tag, no comparisons.
   - **Empty** (`phone_gap is None`): `📵 No phone health data in the last
     30 days — check Health Auto Export.`
4. Wrist/watch logic untouched. When both phone and wrist are stale the
   section shows the 📵 line followed by the existing ⌚ line.

## Testing

`test_health.py` gains a `PhoneStateTests` case class (stdlib unittest,
pure-render style used by `WatchStateTests`):

- fresh phone data → output identical in shape to current behavior
  (comparisons present, no 📵 line)
- stale phone data (gap > 2 d) → 📵 line with day count + last-data date;
  no comparisons; no activity tag; last-known line present
- boundary: gap == 2 renders fresh; gap == 3 renders stale
- all activity metrics empty → 30-day empty message
- stale phone + stale wrist → both 📵 and ⌚ lines render

## Ops (same session, after merge-ready PR)

- Deploy `health.py` only to `hostinger:~/digests/morning/` (scp, per box
  convention). `morning.py` is NOT deployed from this branch: the box runs
  the unmerged `bug-fix/bella-fi-normalization` code plus an uncommitted
  Telegram-delivery patch, which is being committed separately as a
  stacked branch.
- Disable + stop the vestigial `hae-tunnel.service` on the box (script
  deleted; Caddy + DNS serve hae.nebulaai.co directly with a valid cert).
- Verify with a live render on the box: stale state must show the 📵 line.

## Data-resume steps (Shawn, phone side)

Open Health Auto Export on the iPhone → Automations → confirm REST API
target `https://hae.nebulaai.co/api/data` with the `api-key` header (write
token) → re-enable → run a manual export. Watch metrics (HRV / resting HR /
sleep) additionally resume only once the watch is worn again (~7-day
baseline rebuild — the digest already explains this).
