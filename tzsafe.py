"""Timezone resolution that survives a stdlib-only sandbox with no IANA tz database.

Some cloud routine sandboxes ship without the system zoneinfo files and without
the ``tzdata`` package, so ``ZoneInfo("America/Los_Angeles")`` raises
``ZoneInfoNotFoundError``. The digest modules build their timezone at *import*
time, so that turns into an import-time crash and the whole run produces no
output (the Call Retro then DMs "data unavailable"). Fall back to a fixed US
Pacific offset (PDT, UTC-7) so the digest still runs; the worst case is a ~1h
skew on a day-window boundary around a DST change, which is acceptable versus
the entire digest failing.

Mirrors the ``try: ZoneInfo / except: timezone(-7)`` guard the daily/weekly
retro aggregator scripts already carry for this same sandbox.
"""
from __future__ import annotations

from datetime import timedelta, timezone

try:
    from zoneinfo import ZoneInfo
except Exception:  # zoneinfo module itself unavailable (very old Python)
    ZoneInfo = None

# PDT. Pacific is the digest's home zone; a fixed offset is a safe last resort.
PACIFIC_FALLBACK = timezone(timedelta(hours=-7))


def resolve(name: str = "America/Los_Angeles"):
    """A tzinfo for ``name``, or a fixed Pacific offset when the tz database is
    missing — so importing a digest module can never crash on the timezone."""
    if ZoneInfo is None:
        return PACIFIC_FALLBACK
    try:
        return ZoneInfo(name)
    except Exception:
        return PACIFIC_FALLBACK
