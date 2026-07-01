"""tzsafe: timezone resolution must survive a sandbox with no IANA tz database.

Regression guard for the 2026-07-01 Call Retro "data unavailable" incident: the
cloud sandbox lost its tz database, so the module-level ``ZoneInfo(...)`` in
retro/morning/meetings crashed at import and every run produced no output.
"""
import subprocess
import sys
import textwrap
import unittest
from unittest import mock

import tzsafe


class TzSafeTests(unittest.TestCase):
    def test_resolve_returns_a_tzinfo_when_available(self):
        from datetime import datetime, tzinfo
        tz = tzsafe.resolve("America/Los_Angeles")
        self.assertIsInstance(tz, tzinfo)
        # usable: an aware datetime can be built without error
        self.assertIsNotNone(datetime(2026, 7, 1, tzinfo=tz).utcoffset())

    def test_falls_back_when_zoneinfo_raises(self):
        with mock.patch.object(tzsafe, "ZoneInfo", side_effect=Exception("no tz db")):
            self.assertIs(tzsafe.resolve("America/Los_Angeles"), tzsafe.PACIFIC_FALLBACK)

    def test_falls_back_when_zoneinfo_module_missing(self):
        with mock.patch.object(tzsafe, "ZoneInfo", None):
            self.assertIs(tzsafe.resolve("America/Los_Angeles"), tzsafe.PACIFIC_FALLBACK)

    def test_digest_modules_import_without_tz_database(self):
        """retro/morning/meetings build their tz at import — importing them in a
        tz-less environment (ZoneInfo raises) must NOT crash. Run in a subprocess
        so the monkeypatch can't leak into the rest of the suite."""
        code = textwrap.dedent("""
            import zoneinfo
            def boom(*a, **k):
                raise zoneinfo.ZoneInfoNotFoundError("no tz db")
            zoneinfo.ZoneInfo = boom          # simulate a sandbox with no tz database
            import retro, morning, meetings    # must not raise at import
            assert retro.RETRO_TZ is not None
            assert morning.PT is not None and meetings.PT is not None
            print("OK")
        """)
        p = subprocess.run([sys.executable, "-c", code], capture_output=True,
                           text=True, cwd=".")
        self.assertEqual(p.returncode, 0, f"import crashed:\n{p.stderr}")
        self.assertIn("OK", p.stdout)


if __name__ == "__main__":
    unittest.main()
