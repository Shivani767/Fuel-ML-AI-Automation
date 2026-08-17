import json
import tempfile
import unittest
from pathlib import Path

from parse_user_theft_cases import build_verified_theft_events, merge_verified_events_into_event_feed


class TheftEventPipelineTests(unittest.TestCase):
    def test_merge_verified_theft_events_into_event_feed(self):
        verified_events = build_verified_theft_events([
            (1, "Demo Vehicle", 123456789, "2026-06-01", 5.5)
        ])

        with tempfile.TemporaryDirectory() as tmp_dir:
            events_path = Path(tmp_dir) / "fuel_events.json"
            initial_events = [
                {
                    "eventType": "Fuel Theft",
                    "deviceName": "Existing Vehicle",
                    "eventTime": "2026-06-02T00:00:00",
                    "fuelDecrease": 1.1,
                }
            ]
            events_path.write_text(json.dumps(initial_events), encoding="utf-8")

            merged_events, added_events = merge_verified_events_into_event_feed(
                verified_events,
                str(events_path),
            )

            self.assertEqual(len(added_events), 1)
            self.assertTrue(any(event["deviceName"] == "Demo Vehicle" for event in merged_events))
            self.assertEqual(len(merged_events), 2)


if __name__ == "__main__":
    unittest.main()
