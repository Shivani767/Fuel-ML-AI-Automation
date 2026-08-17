import unittest
from unittest.mock import patch

from app import app, device_data_dict, aggregate_to_slabs


class DashboardReportTests(unittest.TestCase):
    def setUp(self):
        self.client = app.test_client()

    def test_day_and_month_report_endpoints_return_csv(self):
        day_response = self.client.get('/api/reports/day-summary')
        month_response = self.client.get('/api/reports/month-summary')

        self.assertEqual(day_response.status_code, 200)
        self.assertEqual(month_response.status_code, 200)
        self.assertIn('text/csv', day_response.content_type)
        self.assertIn('text/csv', month_response.content_type)
        self.assertIn('date,deviceName,eventType,volume,events', day_response.get_data(as_text=True))
        self.assertIn('month,deviceName,eventType,volume,events', month_response.get_data(as_text=True))

    def test_live_endpoint_returns_points_without_fetch(self):
        device_name = next(iter(device_data_dict.keys()))
        response = self.client.get(
            f'/api/live/{device_name}?fetch=0&hours=999999&limit=10&slab=30'
        )
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload['deviceName'], device_name)
        self.assertIn('points', payload)
        self.assertTrue(len(payload['points']) <= 10)
        self.assertEqual(payload.get('slabMinutes'), 30)
        self.assertIsNone(payload.get('error'))

    @patch('app._fetch_live_for_device')
    def test_live_endpoint_reports_fetch_error(self, mock_fetch):
        device_name = next(iter(device_data_dict.keys()))
        mock_fetch.return_value = (None, 'IMEI not mapped for this device. Open /api/live/resolve-map once, then retry.')
        response = self.client.get(f'/api/live/{device_name}?fetch=1&hours=1&limit=5')
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertIn('IMEI not mapped', payload['error'])

    def test_aggregate_to_slabs_collapses_dense_points(self):
        points = [
            {"time": "2026-07-19T10:01:00", "fuelInLiter": 100.0, "iO_1": 1},
            {"time": "2026-07-19T10:10:00", "fuelInLiter": 99.5, "iO_1": 1},
            {"time": "2026-07-19T10:29:00", "fuelInLiter": 98.0, "iO_1": 0},
            {"time": "2026-07-19T10:31:00", "fuelInLiter": 97.0, "iO_1": 0},
            {"time": "2026-07-19T10:55:00", "fuelInLiter": 96.0, "iO_1": 1},
        ]
        slabs = aggregate_to_slabs(points, slab_minutes=30)
        self.assertEqual(len(slabs), 2)
        self.assertEqual(slabs[0]["time"], "2026-07-19T10:00:00")
        self.assertEqual(slabs[0]["fuelInLiter"], 98.0)
        self.assertEqual(slabs[1]["time"], "2026-07-19T10:30:00")
        self.assertEqual(slabs[1]["fuelInLiter"], 96.0)

    def test_device_endpoint_defaults_to_30_min_slabs(self):
        device_name = next(iter(device_data_dict.keys()))
        slabbed = self.client.get(f'/api/device/{device_name}?slab=30').get_json()
        raw = self.client.get(f'/api/device/{device_name}?slab=0').get_json()
        self.assertTrue(len(slabbed) <= len(raw))
        if slabbed:
            self.assertEqual(slabbed[0].get('slabMinutes'), 30)


if __name__ == '__main__':
    unittest.main()
