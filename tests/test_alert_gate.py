"""Edge cases the 30-minute alert gate must get right.

Each test encodes a pattern measured in the real telemetry, not an invented one:
ignition-crank dips that recover, voltage-coupled phantom drops, reporting blackouts,
and the settle/ledger behaviour that makes a recurring agent safe to run.
"""

import sys
import unittest
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend import config  # noqa: E402
from backend.services import alerts, voltage_profile  # noqa: E402

NOW = datetime(2026, 8, 2, 12, 0, 0)


def series(points):
    """points: list of (minutes_before_now, litres, ignition_on)."""
    out = []
    for mins, litres, ign in points:
        out.append({
            "time": (NOW - timedelta(minutes=mins)).isoformat(),
            "fuelInLiter": litres,
            "iO_1": 1 if ign else 0,
            "imei": "TESTIMEI",
            "deviceName": "Test Vehicle",
        })
    return sorted(out, key=lambda p: p["time"])


def steady(start_min, end_min, litres, ign=False, step=2):
    return [(m, litres, ign) for m in range(start_min, end_min - 1, -step)]


def theft_event(volume, start_min, end_min=None):
    end_min = start_min - 5 if end_min is None else end_min
    return {
        "eventType": "Fuel Theft",
        "eventTime": (NOW - timedelta(minutes=start_min)).isoformat(),
        "endTime": (NOW - timedelta(minutes=end_min)).isoformat(),
        "volume": volume,
        "decision": "AUTO_CONFIRMED",
    }


class AlertGateTests(unittest.TestCase):
    def setUp(self):
        # Isolate from any profiles built from real data.
        voltage_profile._cache = {}

    # -- ignition crank transient -------------------------------------------------

    def test_ignition_dip_that_recovers_is_suppressed(self):
        """68% of >2L dips at ignition start come back. Those are not theft."""
        pts = (steady(120, 66, 100.0, ign=False)
               + [(65, 100.0, False), (64, 77.0, True)]      # crank, 23L dip
               + steady(62, 40, 78.0, ign=True)
               + steady(38, 0, 99.5, ign=True))              # recovered
        status, reason = alerts.confirm_event(
            "Test Vehicle", "TESTIMEI", theft_event(23.0, 65, 60), series(pts), now=NOW)
        self.assertEqual(status, "suppress")
        self.assertIn("recovered", reason)

    def test_ignition_dip_that_persists_is_released(self):
        """Same shape, but the fuel never comes back -> a real loss."""
        pts = (steady(120, 66, 100.0, ign=False)
               + [(65, 100.0, False), (64, 77.0, True)]
               + steady(62, 0, 77.0, ign=True))              # stays down
        status, reason = alerts.confirm_event(
            "Test Vehicle", "TESTIMEI", theft_event(23.0, 65, 60), series(pts), now=NOW)
        self.assertEqual(status, "release", reason)

    def test_small_drop_at_crank_suppressed_even_if_persistent(self):
        """A modest persistent step right at crank is still ambiguous."""
        # swing*3 = 9 → crank floor 9 L; volume 6 clears the 5 L theft floor but not crank.
        voltage_profile._cache = {"TESTIMEI": {"swing_liters": 3.0, "coupled": True}}
        pts = (steady(120, 66, 100.0, ign=False)
               + [(65, 100.0, False), (64, 94.0, True)]
               + steady(62, 0, 94.0, ign=True))
        status, reason = alerts.confirm_event(
            "Test Vehicle", "TESTIMEI", theft_event(6.0, 65, 60), series(pts), now=NOW)
        self.assertEqual(status, "suppress")
        self.assertIn("ignition_transient", reason)

    # -- voltage coupling ---------------------------------------------------------

    def test_drop_within_voltage_swing_is_suppressed(self):
        """A device whose reading tracks the rail cannot resolve a small loss."""
        voltage_profile._cache = {"TESTIMEI": {"swing_liters": 8.6, "coupled": True}}
        pts = steady(120, 40, 100.0, ign=True) + steady(38, 0, 93.0, ign=True)
        status, reason = alerts.confirm_event(
            "Test Vehicle", "TESTIMEI", theft_event(7.0, 40, 35), series(pts), now=NOW)
        self.assertEqual(status, "suppress")
        self.assertIn("voltage_artifact", reason)

    def test_large_drop_clears_voltage_swing(self):
        """Well above the coupling floor, the same device can still report theft."""
        voltage_profile._cache = {"TESTIMEI": {"swing_liters": 8.6, "coupled": True}}
        pts = steady(120, 40, 100.0, ign=True) + steady(38, 0, 50.0, ign=True)
        status, reason = alerts.confirm_event(
            "Test Vehicle", "TESTIMEI", theft_event(50.0, 40, 35), series(pts), now=NOW)
        self.assertEqual(status, "release", reason)

    # -- observability ------------------------------------------------------------

    def test_reporting_gap_suppresses(self):
        """A 206-min blackout was seen in real data; nothing observed, nothing claimed."""
        pts = steady(400, 380, 100.0, ign=False) + steady(60, 0, 83.0, ign=False)
        status, reason = alerts.confirm_event(
            "Test Vehicle", "TESTIMEI", theft_event(17.0, 380, 370), series(pts), now=NOW)
        self.assertEqual(status, "suppress")
        self.assertIn("unobserved_gap", reason)

    def test_silent_device_never_reported(self):
        """Absence of data must not read as absence of theft."""
        pts = steady(400, 300, 100.0, ign=False)      # nothing for 5 hours
        status, reason = alerts.confirm_event(
            "Test Vehicle", "TESTIMEI", theft_event(20.0, 380, 370), series(pts), now=NOW)
        self.assertEqual(status, "suppress")
        self.assertEqual(reason, "device_not_reporting")

    # -- settle / timing ----------------------------------------------------------

    def test_recent_event_is_held_not_dropped(self):
        """Too young to judge -> revisit next cycle, never discard."""
        pts = steady(120, 6, 100.0, ign=False) + steady(4, 0, 70.0, ign=False)
        status, reason = alerts.confirm_event(
            "Test Vehicle", "TESTIMEI", theft_event(30.0, 6, 5), series(pts), now=NOW)
        self.assertEqual(status, "hold")
        self.assertEqual(reason, "awaiting_settle")

    def test_held_event_releases_once_settled(self):
        """The same event, judged later, is allowed through."""
        pts = steady(180, 66, 100.0, ign=False) + steady(64, 0, 70.0, ign=False)
        ev = theft_event(30.0, 65, 60)  # 5 min parked drain — not a moving-short spike
        ev["ignition"] = "off"
        status, reason = alerts.confirm_event(
            "Test Vehicle", "TESTIMEI", ev, series(pts), now=NOW)
        self.assertEqual(status, "release", reason)

    # -- fills --------------------------------------------------------------------

    def test_fill_does_not_require_persistence(self):
        pts = steady(120, 40, 50.0, ign=False) + steady(38, 0, 100.0, ign=False)
        ev = theft_event(50.0, 40, 35)
        ev["eventType"] = "Fuel Filling"
        status, reason = alerts.confirm_event(
            "Test Vehicle", "TESTIMEI", ev, series(pts), now=NOW)
        self.assertEqual(status, "release", reason)

    # -- ledger -------------------------------------------------------------------

    def test_event_key_is_stable_and_device_scoped(self):
        ev = theft_event(30.0, 65, 60)
        self.assertEqual(alerts._event_key("A", ev), alerts._event_key("A", dict(ev)))
        self.assertNotEqual(alerts._event_key("A", ev), alerts._event_key("B", ev))


class DailyCsvTests(unittest.TestCase):
    """Alerts are filed per calendar day, by when the event happened."""

    def setUp(self):
        import tempfile
        self._tmp = tempfile.TemporaryDirectory()
        self._orig = alerts.DAILY_DIR
        alerts.DAILY_DIR = Path(self._tmp.name)

    def tearDown(self):
        alerts.DAILY_DIR = self._orig
        self._tmp.cleanup()

    def _ev(self, day, vol=30.0, device="Truck A", reason=None):
        e = {
            "deviceName": device, "imei": "IMEI1", "eventType": "Fuel Theft",
            "eventTime": f"{day}T14:05:00", "endTime": f"{day}T14:12:00",
            "volume": vol, "confidence": 0.8, "decision": "AUTO_CONFIRMED",
            "confirmedAt": f"{day}T14:40:00",
        }
        if reason:
            e["suppressedReason"] = reason
        return e

    def test_events_split_into_one_file_per_day(self):
        alerts.write_daily_csvs(
            [self._ev("2026-08-01"), self._ev("2026-08-02", 40.0)], []
        )
        self.assertTrue(alerts.alerts_csv_for("2026-08-01").exists())
        self.assertTrue(alerts.alerts_csv_for("2026-08-02").exists())
        self.assertEqual(len(alerts.read_daily_alerts("2026-08-01")), 1)
        self.assertEqual(alerts.available_days(), ["2026-08-02", "2026-08-01"])

    def test_day_comes_from_event_time_not_run_time(self):
        """An event at 23:50 confirmed after midnight belongs to the day it happened."""
        late = self._ev("2026-08-01")
        late["eventTime"] = "2026-08-01T23:50:00"
        late["confirmedAt"] = "2026-08-02T00:20:00"     # noticed the next day
        alerts.write_daily_csvs([late], [])
        self.assertTrue(alerts.alerts_csv_for("2026-08-01").exists())
        self.assertFalse(alerts.alerts_csv_for("2026-08-02").exists())

    def test_filtered_events_go_to_a_separate_file(self):
        alerts.write_daily_csvs(
            [self._ev("2026-08-02")],
            [self._ev("2026-08-02", 6.0, reason="voltage_artifact")],
        )
        self.assertTrue(alerts.filtered_csv_for("2026-08-02").exists())
        # Released reads stay clean unless filtered rows are asked for.
        self.assertEqual(len(alerts.read_daily_alerts("2026-08-02")), 1)
        both = alerts.read_daily_alerts("2026-08-02", include_filtered=True)
        self.assertEqual(len(both), 2)
        self.assertEqual(
            {r["status"] for r in both}, {"released", "filtered"}
        )
        filtered = [r for r in both if r["status"] == "filtered"][0]
        self.assertEqual(filtered["suppressedReason"], "voltage_artifact")

    def test_appending_keeps_one_header(self):
        alerts.write_daily_csvs([self._ev("2026-08-02")], [])
        alerts.write_daily_csvs([self._ev("2026-08-02", 44.0, device="Truck B")], [])
        text = alerts.alerts_csv_for("2026-08-02").read_text()
        self.assertEqual(text.count("deviceName"), 1, "header written more than once")
        self.assertEqual(len(alerts.read_daily_alerts("2026-08-02")), 2)


class PipelineOrderTests(unittest.TestCase):
    """The point of merging the timers is that alerts see freshly fetched data.

    If the scan could run before the fetch, the chain would be no better than the
    two independent timers it replaced.
    """

    def test_cycle_fetches_before_it_alerts(self):
        from backend.services import pipeline

        calls = []

        def fake_refresh(*a, **kw):
            calls.append("fetch")
            return {"ok": True, "devices": 80, "fetched": 2000, "skippedSilent": 22}

        def fake_cycle(*a, **kw):
            calls.append("alert")
            return {"theftCount": 1, "fillCount": 2, "heldAwaitingSettle": 0,
                    "suppressedCount": 3, "daysWritten": ["2026-08-02"]}

        import backend.services.alerts as alerts_mod
        import backend.services.fuelark_live as live_mod
        orig_refresh = live_mod.refresh_fleet_live_snapshot
        orig_cycle = alerts_mod.run_cycle
        live_mod.refresh_fleet_live_snapshot = fake_refresh
        alerts_mod.run_cycle = fake_cycle
        try:
            out = pipeline.run_once()
        finally:
            live_mod.refresh_fleet_live_snapshot = orig_refresh
            alerts_mod.run_cycle = orig_cycle

        self.assertEqual(calls, ["fetch", "alert"], "fetch must precede the scan")
        self.assertTrue(out["fetch"]["ok"])
        self.assertEqual(out["alerts"]["theft"], 1)

    def test_failed_fetch_still_runs_the_scan(self):
        """A FuelArk outage must not also stop reporting on data already held."""
        from backend.services import pipeline

        import backend.services.alerts as alerts_mod
        import backend.services.fuelark_live as live_mod
        orig_refresh = live_mod.refresh_fleet_live_snapshot
        orig_cycle = alerts_mod.run_cycle
        live_mod.refresh_fleet_live_snapshot = lambda *a, **kw: (_ for _ in ()).throw(
            RuntimeError("api down"))
        alerts_mod.run_cycle = lambda *a, **kw: {"theftCount": 0, "fillCount": 0,
                                                 "heldAwaitingSettle": 0,
                                                 "suppressedCount": 0, "daysWritten": []}
        try:
            out = pipeline.run_once()
        finally:
            live_mod.refresh_fleet_live_snapshot = orig_refresh
            alerts_mod.run_cycle = orig_cycle

        self.assertFalse(out["fetch"]["ok"])
        self.assertIn("api down", out["fetch"]["error"])
        self.assertIn("theft", out["alerts"])       # the scan still ran

    def test_online_window_exceeds_the_cadence(self):
        """Cache must not age past the online window between fetches.

        This is the stale-cache failure that once showed the whole fleet inactive.
        """
        from backend import config

        cadence_min = config.PIPELINE_INTERVAL_SECONDS / 60.0
        self.assertGreater(
            config.ACTIVE_ONLINE_MINUTES, cadence_min,
            "ACTIVE_ONLINE_MINUTES must exceed the pipeline cadence plus runtime",
        )


def fill_event(volume, start_min, end_min=None, **extra):
    end_min = start_min - 5 if end_min is None else end_min
    row = {
        "eventType": "Fuel Filling",
        "eventTime": (NOW - timedelta(minutes=start_min)).isoformat(),
        "endTime": (NOW - timedelta(minutes=end_min)).isoformat(),
        "volume": volume,
        "decision": "AUTO_CONFIRMED",
    }
    row.update(extra)
    return row


class PrecisionGateTests(unittest.TestCase):
    """Field-level precision gates added for alert-agent precision."""

    def setUp(self):
        voltage_profile._cache = {}

    def test_theft_below_liter_floor_suppressed(self):
        pts = steady(120, 40, 100.0, ign=False) + steady(38, 0, 96.0, ign=False)
        ev = theft_event(4.0, 40, 35)
        ev["durationMinutes"] = 5.0
        ev["ignition"] = "off"
        status, reason = alerts.confirm_event(
            "Test Vehicle", "TESTIMEI", ev, series(pts), now=NOW)
        self.assertEqual(status, "suppress")
        self.assertIn("theft_below", reason)

    def test_spike_drop_suppressed(self):
        pts = steady(120, 40, 100.0, ign=False) + steady(38, 0, 70.0, ign=False)
        ev = theft_event(30.0, 40, 39.8)
        ev["durationMinutes"] = 0.2
        ev["ignition"] = "off"
        status, reason = alerts.confirm_event(
            "Test Vehicle", "TESTIMEI", ev, series(pts), now=NOW)
        self.assertEqual(status, "suppress")
        self.assertIn("spike_", reason)

    def test_moving_fill_suppressed(self):
        pts = steady(120, 0, 100.0, ign=True)
        ev = fill_event(20.0, 40, 35, speed=40.0, durationMinutes=5.0, ignition="on")
        status, reason = alerts.confirm_event(
            "Test Vehicle", "TESTIMEI", ev, series(pts), now=NOW)
        self.assertEqual(status, "suppress")
        self.assertIn("moving_fill", reason)

    def test_ml_noise_disagreement_suppressed(self):
        pts = steady(120, 0, 100.0, ign=False)
        ev = fill_event(20.0, 40, 35, mlLabel="noise", mlConfidence=0.92, speed=0)
        status, reason = alerts.confirm_event(
            "Test Vehicle", "TESTIMEI", ev, series(pts), now=NOW)
        self.assertEqual(status, "suppress")
        self.assertIn("ml_noise", reason)

    def test_moving_short_theft_suppressed(self):
        pts = steady(120, 40, 100.0, ign=True) + steady(38, 0, 90.0, ign=True)
        ev = theft_event(10.0, 40, 38)
        ev["durationMinutes"] = 2.0
        ev["ignition"] = "on"
        ev["trulyParked"] = False
        status, reason = alerts.confirm_event(
            "Test Vehicle", "TESTIMEI", ev, series(pts), now=NOW)
        self.assertEqual(status, "suppress")
        self.assertIn("moving_short_theft", reason)

    def test_parked_theft_not_caught_by_moving_short(self):
        """Ignition-off short drain still reaches persistence / release path."""
        pts = steady(120, 40, 100.0, ign=False) + steady(38, 0, 90.0, ign=False)
        ev = theft_event(10.0, 40, 38)
        ev["durationMinutes"] = 2.0
        ev["ignition"] = "off"
        ev["trulyParked"] = True
        status, reason = alerts.confirm_event(
            "Test Vehicle", "TESTIMEI", ev, series(pts), now=NOW)
        self.assertEqual(status, "release", reason)
        self.assertEqual(alerts.precision_gate_preview(ev), "")

    def test_precision_preview_matches_today_style_rows(self):
        self.assertIn("moving_short", alerts.precision_gate_preview({
            "eventType": "Fuel Theft", "volume_L": 8.97, "durationMinutes": 2.2,
            "ignition": "on", "trulyParked": "False",
        }))
        self.assertIn("spike_", alerts.precision_gate_preview({
            "eventType": "Fuel Theft", "volume_L": 22.72, "durationMinutes": 0.2,
            "ignition": "on", "trulyParked": "False",
        }))
        self.assertEqual(alerts.precision_gate_preview({
            "eventType": "Fuel Filling", "volume_L": 30.0, "durationMinutes": 6.7,
            "ignition": "on", "trulyParked": "True", "speed": 0,
        }), "")


if __name__ == "__main__":
    unittest.main(verbosity=2)
