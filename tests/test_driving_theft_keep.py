"""Sustained site-work drains must not be wiped as 'driving artifacts'.

FuelArk marks large/slow-moving losses (e.g. Lalit RMC Sonipat TM 2026-08-20:
~8–12 L over 10–35 min at ~10 km/h). The old speed>3 blanket drop hid them.
"""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.services.detection import _suppress_driving_artifacts  # noqa: E402


def theft(**kwargs):
    base = {
        "eventType": "Fuel Theft",
        "eventTime": "2026-08-20 12:04:20",
        "endTime": "2026-08-20 12:39:05",
        "volume": 12.38,
        "confidence": 0.79,
        "decision": "AUTO_CONFIRMED",
        "ignition": "on",
        "speed": 10.0,
        "durationMinutes": 34.8,
        "trulyParked": False,
        "mlLabel": "theft",
        "mlConfidence": 0.85,
    }
    base.update(kwargs)
    return base


class DrivingTheftKeepTests(unittest.TestCase):
    def test_lalit_style_sustained_moving_theft_kept(self):
        out = _suppress_driving_artifacts([theft()])
        self.assertEqual(len(out), 1)
        self.assertAlmostEqual(out[0]["volume"], 12.38)

    def test_short_small_moving_blip_dropped(self):
        out = _suppress_driving_artifacts([
            theft(volume=6.0, speed=12.0, durationMinutes=1.0, trulyParked=False),
        ])
        self.assertEqual(out, [])

    def test_large_fill_while_crawling_kept(self):
        fill = {
            "eventType": "Fuel Filling",
            "volume": 50.45,
            "speed": 7.0,
            "ignition": "on",
            "trulyParked": False,
            "durationMinutes": 2.2,
            "decision": "AUTO_CONFIRMED",
        }
        out = _suppress_driving_artifacts([fill])
        self.assertEqual(len(out), 1)

    def test_highway_fill_dropped(self):
        fill = {
            "eventType": "Fuel Filling",
            "volume": 12.0,
            "speed": 40.0,
            "ignition": "on",
            "trulyParked": False,
            "durationMinutes": 2.0,
            "decision": "AUTO_CONFIRMED",
        }
        out = _suppress_driving_artifacts([fill])
        self.assertEqual(out, [])


if __name__ == "__main__":
    unittest.main()
