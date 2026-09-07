"""Fuel fills of 10 L or less are ignored on every path.

Sub-10 L rises on this sender are slope rebound, slosh and drift rather than a
pump, so no detector path, and no alert, may surface them.
"""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend import config  # noqa: E402
from backend.services.alerts import precision_gate_preview  # noqa: E402
from backend.services.detection import (  # noqa: E402
    _drop_small_fills,
    _heuristic_events,
    _keep_session_row,
    _suppress_driving_artifacts,
)

FLOOR = config.ALERT_FILL_MIN_LITERS


def session(**kwargs):
    base = {
        "type": "FILL",
        "decision": "AUTO_CONFIRMED",
        "magnitude": 42.0,
        "raw_magnitude": 42.0,
        "start_time": "2026-08-20 03:10:00",
        "end_time": "2026-08-20 03:18:00",
        "confidence": 0.9,
        "ignition": "off",
        "speed_kmh": 0.0,
        "truly_parked": True,
    }
    base.update(kwargs)
    return base


def fill(**kwargs):
    base = {
        "eventType": "Fuel Filling",
        "eventTime": "2026-08-20 03:10:00",
        "endTime": "2026-08-20 03:18:00",
        "volume": 40.0,
        "confidence": 0.9,
        "decision": "AUTO_CONFIRMED",
        "ignition": "off",
        "speed": 0.0,
        "trulyParked": True,
    }
    base.update(kwargs)
    return base


class SessionRowFloorTests(unittest.TestCase):
    def keep(self, **kwargs):
        return _keep_session_row(session(**kwargs), theft_min=5.0, fill_min=FLOOR)

    def test_small_fill_dropped(self):
        self.assertIsNone(self.keep(magnitude=7.5, raw_magnitude=7.5))

    def test_fill_exactly_at_floor_dropped(self):
        self.assertIsNone(self.keep(magnitude=FLOOR, raw_magnitude=FLOOR))

    def test_smoothed_below_floor_uses_raw(self):
        row = self.keep(magnitude=7.0, raw_magnitude=18.0)
        self.assertIsNotNone(row)
        self.assertAlmostEqual(row["volume"], 18.0)

    def test_raw_below_floor_but_smoothed_above_kept(self):
        row = self.keep(magnitude=12.0, raw_magnitude=9.0)
        self.assertIsNotNone(row)
        self.assertAlmostEqual(row["volume"], 12.0)

    def test_small_theft_still_uses_theft_floor(self):
        row = _keep_session_row(
            session(type="DRAIN", magnitude=6.0, raw_magnitude=6.0),
            theft_min=5.0,
            fill_min=FLOOR,
        )
        self.assertIsNotNone(row)
        self.assertEqual(row["eventType"], "Fuel Theft")


class HeuristicFloorTests(unittest.TestCase):
    def points(self, rise):
        parked = {"iO_1": 0, "speed": 0, "iO_2": 0, "iO_6": 0}
        return [
            {"time": "2026-08-20 03:10:00", "fuelInLiter": 100.0, **parked},
            {"time": "2026-08-20 03:20:00", "fuelInLiter": 100.0 + rise, **parked},
        ]

    def test_small_parked_rise_ignored(self):
        out = _heuristic_events(self.points(FLOOR - 1), theft_min=5.0, fill_min=FLOOR)
        self.assertEqual(out, [])

    def test_rise_at_floor_ignored(self):
        out = _heuristic_events(self.points(FLOOR), theft_min=5.0, fill_min=FLOOR)
        self.assertEqual(out, [])

    def test_real_fill_kept(self):
        out = _heuristic_events(self.points(35.0), theft_min=5.0, fill_min=FLOOR)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["eventType"], "Fuel Filling")


class ArtifactAndFinalFloorTests(unittest.TestCase):
    def test_parked_small_fill_dropped_as_artifact(self):
        self.assertEqual(_suppress_driving_artifacts([fill(volume=6.0)]), [])

    def test_real_parked_fill_kept(self):
        self.assertEqual(len(_suppress_driving_artifacts([fill()])), 1)

    def test_final_pass_drops_small_fills_from_any_source(self):
        events = [
            fill(volume=4.0, decision="VERIFIED", source="verified_theft_events"),
            fill(volume=FLOOR),
            fill(volume=FLOOR + 0.5),
            {"eventType": "Fuel Theft", "volume": 6.0},
        ]
        kept = _drop_small_fills(events, FLOOR)
        self.assertEqual(
            [(e["eventType"], e["volume"]) for e in kept],
            [("Fuel Filling", FLOOR + 0.5), ("Fuel Theft", 6.0)],
        )


class AlertGateFloorTests(unittest.TestCase):
    def test_small_fill_suppressed_with_reason(self):
        reason = precision_gate_preview(fill(volume=8.0))
        self.assertEqual(reason, f"fill_at_or_below_{FLOOR:.0f}L")

    def test_fill_at_floor_suppressed(self):
        self.assertTrue(precision_gate_preview(fill(volume=FLOOR)))

    def test_real_fill_released(self):
        self.assertEqual(precision_gate_preview(fill(volume=FLOOR + 5)), "")


if __name__ == "__main__":
    unittest.main()
