"""Behavior tests for the cloud Bella sync command."""

import unittest

import bella
import bella_sync


SNAPSHOT = bella.BellaSnapshot(
    pet_id="pet-123",
    pet_name="Bella",
    synced_at="2026-06-12T12:00:00+00:00",
    series={"steps": {"2026-06-12": 8421.0}},
    directions={},
)


class RecordingClient:
    def __init__(self):
        self.calls = []

    def upsert_health_metric(self, **kwargs):
        self.calls.append(kwargs)


class BellaSyncCommandTests(unittest.TestCase):
    def test_dry_run_collects_without_constructing_corpus_client(self):
        output = []

        def forbidden_client():
            raise AssertionError("dry run constructed a Corpus client")

        code = bella_sync.main(
            ["--date", "2026-06-12", "--dry-run"],
            collect=lambda day, **kwargs: SNAPSHOT,
            client_factory=forbidden_client,
            output=output.append,
            error=output.append,
        )

        self.assertEqual(code, 0)
        text = "\n".join(output)
        self.assertIn("Bella", text)
        self.assertIn("2026-06-12", text)
        self.assertIn("bella_steps", text)
        self.assertIn("1 row", text)

    def test_live_run_writes_snapshot_to_corpus(self):
        client = RecordingClient()

        code = bella_sync.main(
            ["--date", "2026-06-12"],
            collect=lambda day, **kwargs: SNAPSHOT,
            client_factory=lambda: client,
            output=lambda _: None,
            error=lambda _: None,
        )

        self.assertEqual(code, 0)
        self.assertEqual(len(client.calls), 1)
        self.assertEqual(client.calls[0]["source"], "fi")

    def test_connector_failure_is_bounded_and_does_not_echo_response(self):
        errors = []

        def fail_collect(day, **kwargs):
            raise bella.FiSyncError("secret-password full-json-response")

        code = bella_sync.main(
            ["--date", "2026-06-12"],
            collect=fail_collect,
            client_factory=lambda: RecordingClient(),
            output=lambda _: None,
            error=errors.append,
        )

        self.assertEqual(code, 1)
        text = "\n".join(errors)
        self.assertIn("Fi sync failed", text)
        self.assertNotIn("secret-password", text)
        self.assertNotIn("full-json-response", text)


if __name__ == "__main__":
    unittest.main()
