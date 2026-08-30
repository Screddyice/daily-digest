"""Behavior tests for Bella's Fi snapshot to Corpus boundary."""

import unittest

import bella
import bella_corpus


def _snapshot(series=None):
    return bella.BellaSnapshot(
        pet_id="pet-123",
        pet_name="Bella",
        synced_at="2026-06-12T12:00:00+00:00",
        series=series or {
            "steps": {"2026-06-12": 8421.0},
            "sleep": {"2026-06-11": 500.0},
            "rest_min": {"2026-06-12": 419.0},
            "eating_events": {"2026-06-12": 3.0},
            "licking_min": {"2026-06-12": 1.0},
            "future_field": {"2026-06-12": 99.0},
        },
        directions={},
    )


class SnapshotRowsTests(unittest.TestCase):
    def test_maps_only_allowlisted_series_with_exact_units(self):
        rows = bella_corpus.snapshot_rows(_snapshot())
        compact = {(r.metric, r.value, r.unit, r.day) for r in rows}

        self.assertIn(("bella_steps", 8421.0, "count", "2026-06-12"), compact)
        self.assertIn(("bella_sleep_minutes", 500.0, "min", "2026-06-11"), compact)
        self.assertIn(("bella_eating_events", 3.0, "count", "2026-06-12"), compact)
        self.assertIn(("bella_licking_minutes", 1.0, "min", "2026-06-12"), compact)
        self.assertNotIn("future_field", {r.fi_field for r in rows})

    def test_rejects_negative_nonfinite_and_malformed_values(self):
        snapshot = _snapshot({
            "steps": {
                "2026-06-09": -1,
                "2026-06-10": float("nan"),
                "2026-06-11": "not-a-number",
                "2026-06-12": 0,
            }
        })

        rows = bella_corpus.snapshot_rows(snapshot)

        self.assertEqual([(r.day, r.value) for r in rows], [("2026-06-12", 0.0)])


class RecordingClient:
    def __init__(self):
        self.calls = []

    def upsert_health_metric(self, **kwargs):
        self.calls.append(kwargs)


class WriteSnapshotTests(unittest.TestCase):
    def test_writes_subject_isolated_rows_with_fi_provenance(self):
        client = RecordingClient()
        snapshot = _snapshot({"steps": {"2026-06-12": 8421.0}})

        count = bella_corpus.write_snapshot(snapshot, client)

        self.assertEqual(count, 1)
        self.assertEqual(client.calls, [{
            "metric": "bella_steps",
            "value": 8421.0,
            "unit": "count",
            "date": "2026-06-12",
            "source": "fi",
            "raw": {
                "subject": "Bella",
                "pet_id": "pet-123",
                "synced_at": "2026-06-12T12:00:00+00:00",
                "fi_field": "steps",
            },
        }])


if __name__ == "__main__":
    unittest.main()
