"""Unit tests for health alerting (Shawn + Bella → Telegram). Stdlib only:

    python3 -m unittest test_alerts -v
"""
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

import alerts

TODAY = date(2026, 6, 12)


def _series(end: date, n: int, vals) -> dict:
    if not isinstance(vals, (list, tuple)):
        vals = [vals] * n
    days = [(end - timedelta(days=n - 1 - i)).isoformat() for i in range(n)]
    return dict(zip(days, vals))


def _flat_then(end: date, base: float, recent: list) -> dict:
    n = 14 + len(recent)
    return _series(end, n, [base] * 14 + list(recent))


def _healthy_you() -> dict:
    return {
        "step_count": _series(TODAY, 17, 5000),
        "heart_rate_variability": _series(TODAY, 17, 45),
        "resting_heart_rate": _series(TODAY, 17, 60),
        "blood_oxygen_saturation": _series(TODAY, 17, 97),
        "sleep_analysis": _series(TODAY, 17, 7.5),
    }


def _healthy_bella() -> dict:
    return {"steps": _series(TODAY, 17, 8000), "sleep": _series(TODAY, 17, 700)}


class DetectorTests(unittest.TestCase):
    def test_healthy_data_raises_no_alerts(self):
        self.assertEqual(alerts.detect_alerts(_healthy_you(), _healthy_bella(), TODAY), [])

    def test_illness_combo_alerts(self):
        you = _healthy_you()
        you["heart_rate_variability"] = _flat_then(TODAY, 45, [34, 31, 28])
        you["resting_heart_rate"] = _flat_then(TODAY, 60, [70, 73, 76])
        you["blood_oxygen_saturation"] = _flat_then(TODAY, 97, [94, 93, 93])
        found = alerts.detect_alerts(you, _healthy_bella(), TODAY)
        self.assertTrue(any("fighting something" in a or "illness" in a.lower() for a in found))

    def test_low_blood_oxygen_alerts(self):
        you = _healthy_you()
        you["blood_oxygen_saturation"] = _flat_then(TODAY, 97, [93, 92, 92])
        found = alerts.detect_alerts(you, _healthy_bella(), TODAY)
        self.assertTrue(any("oxygen" in a.lower() for a in found))

    def test_sharp_resting_hr_rise_alerts(self):
        you = _healthy_you()
        you["resting_heart_rate"] = _flat_then(TODAY, 60, [70, 73, 76])
        found = alerts.detect_alerts(you, _healthy_bella(), TODAY)
        self.assertTrue(any("heart" in a.lower() for a in found))

    def test_bella_activity_collapse_alerts(self):
        bella = _healthy_bella()
        bella["steps"] = _flat_then(TODAY, 8000, [4000, 3500, 3000])
        found = alerts.detect_alerts(_healthy_you(), bella, TODAY)
        self.assertTrue(any("Bella" in a for a in found))

    def test_bella_rest_surge_alerts_lethargy(self):
        bella = _healthy_bella()
        bella["sleep"] = _flat_then(TODAY, 700, [950, 1000, 1050])
        found = alerts.detect_alerts(_healthy_you(), bella, TODAY)
        self.assertTrue(any("Bella" in a for a in found))

    def test_alerts_contain_no_numbers(self):
        you = _healthy_you()
        you["blood_oxygen_saturation"] = _flat_then(TODAY, 97, [93, 92, 92])
        for a in alerts.detect_alerts(you, _healthy_bella(), TODAY):
            self.assertNotRegex(a, r"\d")


class EdgeTriggerTests(unittest.TestCase):
    def test_new_alerts_pass_repeats_suppressed(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp) / "alerts.json"
            first = alerts.edge_filter(["low oxygen", "Bella resting more"], state)
            self.assertEqual(first, ["low oxygen", "Bella resting more"])
            second = alerts.edge_filter(["low oxygen"], state)
            self.assertEqual(second, [])  # still active, already alerted

    def test_cleared_then_returning_alert_fires_again(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp) / "alerts.json"
            alerts.edge_filter(["low oxygen"], state)
            alerts.edge_filter([], state)            # condition cleared
            again = alerts.edge_filter(["low oxygen"], state)
            self.assertEqual(again, ["low oxygen"])  # re-fires on new onset


class SendTests(unittest.TestCase):
    def test_send_invokes_hermes_with_message(self):
        captured = {}

        def fake_run(cmd, **kw):
            captured["cmd"] = cmd

            class R:
                returncode = 0
                stderr = ""
            return R()

        alerts.send_telegram(["possible illness pattern building"], run=fake_run)
        self.assertIn("send", captured["cmd"])
        self.assertIn("telegram", " ".join(captured["cmd"]))
        self.assertTrue(any("illness" in c for c in captured["cmd"]))

    def test_send_noop_on_empty(self):
        def boom(cmd, **kw):
            raise AssertionError("should not send")
        alerts.send_telegram([], run=boom)  # must not raise


if __name__ == "__main__":
    unittest.main()
