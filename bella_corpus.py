"""Map a validated Bella Fi snapshot into subject-isolated Corpus rows."""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date

from bella import BellaSnapshot


FI_FIELD_MAP: dict[str, tuple[str, str]] = {
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


@dataclass(frozen=True)
class CorpusRow:
    metric: str
    value: float
    unit: str
    day: str
    fi_field: str


def _valid_value(raw) -> float | None:
    if isinstance(raw, bool):
        return None
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    if value < 0 or not math.isfinite(value):
        return None
    return value


def snapshot_rows(snapshot: BellaSnapshot) -> list[CorpusRow]:
    """Return allowlisted, dated values from a Bella snapshot."""
    rows: list[CorpusRow] = []
    for fi_field, (metric, unit) in FI_FIELD_MAP.items():
        series = snapshot.series.get(fi_field)
        if not isinstance(series, dict):
            continue
        for raw_day, raw_value in sorted(series.items()):
            try:
                day = date.fromisoformat(str(raw_day)).isoformat()
            except ValueError:
                continue
            value = _valid_value(raw_value)
            if value is None:
                continue
            rows.append(CorpusRow(metric, value, unit, day, fi_field))
    return rows


def write_snapshot(snapshot: BellaSnapshot, client: object) -> int:
    """Idempotently upsert all validated snapshot rows into Corpus."""
    rows = snapshot_rows(snapshot)
    for row in rows:
        client.upsert_health_metric(
            metric=row.metric,
            value=row.value,
            unit=row.unit,
            date=row.day,
            source="fi",
            raw={
                "subject": "Bella",
                "pet_id": snapshot.pet_id,
                "synced_at": snapshot.synced_at,
                "fi_field": row.fi_field,
            },
        )
    return len(rows)
