import sys
import unittest
from pathlib import Path

# analyze_fuel_data now lives in legacy/ (quarantined v1 pipeline).
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "legacy"))

from analyze_fuel_data import is_short_window_event


class AnalyzeFuelDataTests(unittest.TestCase):
    def test_short_window_large_drop_is_flagged(self):
        self.assertTrue(is_short_window_event(-8.0, 10.0, "theft"))

    def test_long_window_large_drop_is_not_flagged(self):
        self.assertFalse(is_short_window_event(-8.0, 30.0, "theft"))

    def test_short_window_large_increase_is_flagged(self):
        self.assertTrue(is_short_window_event(5.0, 8.0, "filling"))

    def test_long_window_large_increase_is_not_flagged(self):
        self.assertFalse(is_short_window_event(5.0, 30.0, "filling"))


if __name__ == "__main__":
    unittest.main()
