"""Unit tests against synthetic fuel curves (no API calls)."""

from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta

import numpy as np
import pandas as pd
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from config import DetectorConfig
from detection.scoring import run_detection, DECISION_AUTO, DECISION_REVIEW, DECISION_NOISE
from reports.build_report import safe_avg_consumption, safe_mileage


TZ = "Asia/Kolkata"


def _ts(start: str, n: int, step_seconds: int = 60) -> pd.DatetimeIndex:
    start_dt = pd.Timestamp(start).tz_localize(TZ)
    return pd.date_range(start_dt, periods=n, freq=f"{step_seconds}s")


def _base_frame(timestamps, fuel, *, ignition="off", speed=0.0, movement=False,
                ext_v=24.0, int_v=4.1, imei="999000111222333"):
    n = len(timestamps)
    if isinstance(ignition, str):
        ignition = [ignition] * n
    if np.isscalar(speed):
        speed = [speed] * n
    if np.isscalar(movement):
        movement = [movement] * n
    if np.isscalar(ext_v):
        ext_v = [ext_v] * n
    if np.isscalar(int_v):
        int_v = [int_v] * n

    fuel = np.asarray(fuel, dtype=float)
    # engine hours / distance from consecutive samples
    eh = [0.0]
    dist = [0.0]
    for i in range(1, n):
        dt_h = (timestamps[i] - timestamps[i - 1]).total_seconds() / 3600.0
        eh.append(dt_h if ignition[i] == "on" else 0.0)
        if movement[i] and speed[i] > 0:
            dist.append(speed[i] * dt_h)
        else:
            dist.append(0.0)

    return pd.DataFrame(
        {
            "timestamp": timestamps,
            "imei": imei,
            "vehicle_name_raw": "Synthetic",
            "vehicle_name": "Synthetic",
            "vehicle_type": "truck",
            "fuel_level": fuel,
            "fuel_level_edc": fuel * 100,  # ADC-like scale
            "ignition": ignition,
            "movement": movement,
            "speed_kmh": speed,
            "external_voltage": ext_v,
            "internal_voltage": int_v,
            "engine_hours_delta": eh,
            "distance_km": dist,
            "parked_since": None,
            "soft_alert": None,
            "latitude": 28.0,
            "longitude": 77.0,
        }
    )


class DetectionSyntheticTests(unittest.TestCase):
    def setUp(self):
        self.cfg = DetectorConfig()

    def _run(self, df):
        return run_detection(df, self.cfg)

    def test_slow_linear_decline_no_event(self):
        n = 60
        ts = _ts("2026-06-28 08:00:00", n, 60)
        # ~0.05 L/min while running ≈ expected burn territory
        fuel = 100 - np.linspace(0, 3.0, n)  # slow 3L over an hour
        ign = ["on"] * n
        movement = [True] * n
        speed = [40.0] * n
        df = _base_frame(ts, fuel, ignition=ign, movement=movement, speed=speed)
        _, events = self._run(df)
        auto_drains = events[
            (events["event_type"] == "DRAIN") & (events["decision"] == DECISION_AUTO)
        ] if not events.empty else events
        self.assertTrue(auto_drains.empty, f"Unexpected auto drains: {auto_drains}")

    def test_sharp_drop_plateau_parked_auto_drain(self):
        n = 40
        ts = _ts("2026-06-28 02:00:00", n, 60)
        fuel = np.full(n, 80.0)
        fuel[15:] = 72.0  # 8L drop, no bounce, stable
        df = _base_frame(ts, fuel, ignition="off", movement=False, speed=0.0)
        _, events = self._run(df)
        drains = events[(events["event_type"] == "DRAIN") & (~events["discarded_as_glitch"])]
        self.assertFalse(drains.empty)
        top = drains.sort_values("confidence", ascending=False).iloc[0]
        self.assertEqual(top["decision"], DECISION_AUTO)
        self.assertGreaterEqual(top["quantity_liters"], 5.0)

    def test_sharp_drop_bounce_back_discarded(self):
        n = 30
        ts = _ts("2026-06-28 03:00:00", n, 60)
        fuel = np.full(n, 90.0)
        fuel[10] = 82.0  # spike down
        fuel[11:] = 90.0  # bounce back
        df = _base_frame(ts, fuel, ignition="off")
        _, events = self._run(df)
        if events.empty:
            return
        # Glitch should be noise / discarded
        glitch = events[events["discarded_as_glitch"] == True]  # noqa: E712
        auto = events[
            (events["event_type"] == "DRAIN")
            & (events["decision"] == DECISION_AUTO)
            & (~events["discarded_as_glitch"].fillna(False))
        ]
        self.assertTrue(len(glitch) >= 1 or auto.empty)

    def test_rapid_rise_plateau_auto_fill(self):
        n = 40
        ts = _ts("2026-06-28 10:00:00", n, 60)
        fuel = np.full(n, 40.0)
        fuel[12:] = 55.0  # +15L fill then plateau
        df = _base_frame(ts, fuel, ignition="off")
        _, events = self._run(df)
        fills = events[(events["event_type"] == "FILL") & (~events["discarded_as_glitch"])]
        self.assertFalse(fills.empty)
        top = fills.sort_values("confidence", ascending=False).iloc[0]
        self.assertEqual(top["decision"], DECISION_AUTO)
        self.assertGreaterEqual(top["quantity_liters"], 5.0)

    def test_rapid_rise_no_plateau_manual_review(self):
        n = 40
        ts = _ts("2026-06-28 11:00:00", n, 60)
        fuel = np.full(n, 30.0)
        fuel[10] = 42.0
        # Strong sawtooth afterward — no stable plateau
        for i in range(11, n):
            fuel[i] = 42.0 + (6.0 if i % 2 == 0 else -6.0)
        df = _base_frame(ts, fuel, ignition="off")
        _, events = self._run(df)
        fills = events[
            (events["event_type"] == "FILL")
            & (~events["discarded_as_glitch"].fillna(False))
        ]
        self.assertFalse(fills.empty)
        # Unstable (plateau_ok is False) fills must not be AUTO_CONFIRMED
        unstable = fills[fills["plateau_ok"] == False]  # noqa: E712
        if not unstable.empty:
            self.assertTrue(all(unstable["decision"] != DECISION_AUTO))

    def test_recurrence_small_drains_escalate(self):
        """Three parked ~3L steps should consolidate / surface (not vanish)."""
        from detection.pipeline_v3 import run_session_pipeline

        n = 120
        ts = _ts("2026-06-28 01:00:00", n, 60)
        fuel = np.full(n, 100.0)
        fuel[20:] -= 3.0
        fuel[40:] -= 3.0
        fuel[60:] -= 3.0
        df = _base_frame(ts, fuel, ignition="off", movement=False, speed=0.0)
        _, events = run_session_pipeline(df)
        drains = events[
            (events["event_type"] == "DRAIN")
            & (events["decision"].isin([DECISION_AUTO, DECISION_REVIEW]))
        ]
        self.assertFalse(drains.empty, "expected at least one review/auto drain session")
        # Either one consolidated ≥5L session or multiple small sessions totaling ≥5L
        total = float(drains["quantity_liters"].sum())
        self.assertGreaterEqual(total, 5.0)

    def test_tamper_flag_boosts_confidence(self):
        n = 40
        ts = _ts("2026-06-28 04:00:00", n, 60)

        # Without tamper
        fuel = np.full(n, 70.0)
        fuel[20:] = 63.0
        df_a = _base_frame(ts, fuel, ignition="off", ext_v=24.0)
        _, ev_a = self._run(df_a)
        drain_a = ev_a[(ev_a["event_type"] == "DRAIN") & (~ev_a["discarded_as_glitch"])]
        self.assertFalse(drain_a.empty)
        conf_a = float(drain_a.sort_values("confidence", ascending=False).iloc[0]["confidence"])

        # With sharp external voltage drop (≥8V threshold) just before drain
        ext = np.full(n, 24.0)
        ext[18] = 14.0  # 10V drop
        df_b = _base_frame(ts, fuel, ignition="off", ext_v=ext)
        _, ev_b = self._run(df_b)
        drain_b = ev_b[(ev_b["event_type"] == "DRAIN") & (~ev_b["discarded_as_glitch"])]
        self.assertFalse(drain_b.empty)
        top_b = drain_b.sort_values("confidence", ascending=False).iloc[0]
        self.assertTrue(bool(top_b["tamper_flag"]))
        self.assertGreaterEqual(float(top_b["confidence"]), conf_a)

    def test_ignition_on_matched_burn_no_event(self):
        from detection.scoring import run_detection

        n = 30
        ts = _ts("2026-06-28 09:00:00", n, 60)
        # Legacy residual path: truck running ~3.5 L/h → ~0.058 L per 60s step
        step = 3.5 / 60.0
        fuel = 100 - np.arange(n) * step
        df = _base_frame(
            ts, fuel, ignition=["on"] * n, movement=[True] * n, speed=[50.0] * n
        )
        prepared, events = run_detection(df, self.cfg, use_sessions=False)
        if not events.empty:
            auto = events[
                (events["event_type"] == "DRAIN") & (events["decision"] == DECISION_AUTO)
            ]
            self.assertTrue(auto.empty)
        self.assertTrue((prepared["residual"].abs() < 2.5).mean() > 0.8)

    def test_report_safe_divzero(self):
        self.assertIsNone(safe_avg_consumption(10.0, 0.0))
        self.assertIsNone(safe_avg_consumption(10.0, -1.0))
        self.assertIsNone(safe_mileage(100.0, 0.0))
        self.assertIsNone(safe_mileage(100.0, -5.0))
        self.assertAlmostEqual(safe_avg_consumption(10.0, 2.0), 5.0)
        self.assertAlmostEqual(safe_mileage(100.0, 10.0), 10.0)


class VehicleAssignmentTests(unittest.TestCase):
    def test_time_aware_resolution(self):
        import tempfile
        from data import vehicle_assignments as va

        with tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False) as f:
            f.write(
                "imei,vehicle_name,valid_from,valid_to,vehicle_type\n"
                "111,Alpha,2026-06-01,2026-06-28T11:59:59,truck\n"
                "111,Beta,2026-06-28T12:00:00,,truck\n"
            )
            path = f.name

        assignments = va.load_assignments(path)
        a = va.resolve_vehicle("111", "2026-06-28 10:00:00", assignments)
        b = va.resolve_vehicle("111", "2026-06-28 13:00:00", assignments)
        self.assertEqual(a["vehicle_name"], "Alpha")
        self.assertEqual(b["vehicle_name"], "Beta")
        os.unlink(path)


class SessionPipelineV3Tests(unittest.TestCase):
    def test_continuous_drain_is_one_session_not_many_ticks(self):
        """One continuous 20L drain over many ticks → single session row."""
        from detection.pipeline_v3 import run_session_pipeline

        n = 40
        ts = _ts("2026-06-28 02:00:00", n, 30)
        fuel = np.linspace(100, 80, n)  # smooth continuous drain
        df = _base_frame(ts, fuel, ignition="off", movement=False, speed=0.0)
        _, events = run_session_pipeline(df)
        drains = events[
            (events["event_type"] == "DRAIN")
            & (~events["discarded_as_glitch"].fillna(False))
            & (events["decision"] != "LIKELY_NOISE")
        ]
        self.assertFalse(drains.empty)
        # Must not explode into dozens of tick-events
        self.assertLessEqual(len(drains), 3)
        self.assertGreaterEqual(float(drains["magnitude"].max()), 15.0)

    def test_same_location_required_for_session_merge(self):
        from detection.sessions import build_sessions, same_location

        self.assertTrue(
            same_location(
                {"lat": 28.0, "lon": 77.0},
                {"lat": 28.0001, "lon": 77.0001},
                50,
            )
        )
        self.assertFalse(
            same_location(
                {"lat": 28.0, "lon": 77.0},
                {"lat": 28.01, "lon": 77.01},
                50,
            )
        )


class SplitSessionMergeTests(unittest.TestCase):
    """A momentary counter-tick must not cut one event into fragments.

    Taken from a confirmed 204 L theft (JSPL SMS, 2026-06-13): a single 0.46 L blip
    at 11:24 split it into 87.58 L and 117.33 L, each landing in MANUAL_REVIEW rather
    than one AUTO_CONFIRMED event.
    """

    def _sessions(self, levels, start="2026-06-13 11:00:00", step_s=30):
        import pandas as pd
        from detection.sessions import build_sessions

        ts = pd.date_range(start=start, periods=len(levels), freq=f"{step_s}s")
        df = pd.DataFrame({
            "timestamp": ts,
            "fuel_primary": levels,
            "latitude": [28.0] * len(levels),
            "longitude": [77.0] * len(levels),
            "imei": ["TEST"] * len(levels),
        })
        return build_sessions(df)

    def test_blip_inside_drain_does_not_split_it(self):
        # Mirrors the real trace: 216.3 falls to 128.7, ticks UP 0.5 L, then keeps
        # falling to 11.8. That single rise is what cut the event in two.
        first = [216.3 - 8.0 * i for i in range(11)]      # 216.3 -> 128.3
        first[-1] = 128.7
        second = [129.2 - 10.0 * i for i in range(12)]    # 129.2 -> 11.2
        second[-1] = 11.8
        levels = [216.3] * 4 + first + second + [11.8] * 4

        sessions = self._sessions(levels)
        drains = [s for s in sessions if s["type"] == "DRAIN" and s["magnitude"] > 20]
        self.assertEqual(len(drains), 1, f"expected one merged drain, got {len(drains)}")
        self.assertGreater(drains[0]["magnitude"], 190)
        # The merge path -- not accidental single-session detection -- must be why.
        self.assertGreater(drains[0].get("merged_fragments", 1), 1)

    def test_distinct_events_are_not_welded_together(self):
        # two real drains an hour apart must stay separate
        levels = ([200.0] * 4 + list(range(200, 150, -10)) + [150.0] * 120
                  + list(range(150, 100, -10)) + [100.0] * 4)
        sessions = self._sessions([float(v) for v in levels])
        drains = [s for s in sessions if s["type"] == "DRAIN" and s["magnitude"] > 20]
        self.assertEqual(len(drains), 2, "separate events must not merge")

    def test_real_refuel_is_not_absorbed(self):
        # a genuine fill between two drains is far above the bridge tolerance
        levels = ([200.0] * 4 + list(range(200, 150, -10))
                  + [190.0] * 6                       # +40 L refuel, sustained
                  + list(range(190, 140, -10)) + [140.0] * 4)
        sessions = self._sessions([float(v) for v in levels])
        fills = [s for s in sessions if s["type"] == "FILL" and s["magnitude"] > 20]
        self.assertTrue(fills, "a real refuel must survive as its own session")


if __name__ == "__main__":
    unittest.main()
