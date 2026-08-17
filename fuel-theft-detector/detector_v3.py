#!/usr/bin/env python3
"""
Fuel drain / fill event detector for FuelArk telemetry — v3 CLI wrapper.

Uses session consolidation (not per-tick events), dual-channel cross-validation,
tamper voltage signals, and time-aware vehicle assignment.

    export FUELARK_API_KEY="..."
    python detector_v3.py --imei 350424061368641 --from "2026-06-28 00:00:00" --to "2026-06-28 23:59:59"
    python detector_v3.py --imei ... --from ... --to ... --assignment-history data/vehicle_assignments.csv
    python detector_v3.py --imei ... --from ... --to ... --local-json ../fuel_data/live_fuel_data.json
"""

from __future__ import annotations

import argparse
import os
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from data.fetch import fetch_range, load_local_json
from data.vehicle_assignments import attach_vehicle_names
from detection.pipeline_v3 import run_session_pipeline


def main():
    parser = argparse.ArgumentParser(description="FuelArk drain/fill detector v3")
    parser.add_argument("--imei", required=True)
    parser.add_argument("--from", dest="from_dt", required=True)
    parser.add_argument("--to", dest="to_dt", required=True)
    parser.add_argument("--assignment-history", dest="assignment_history", default=None)
    parser.add_argument("--local-json", help="Skip API; load FuelArk JSON dump")
    parser.add_argument("--no-cache", action="store_true")
    args = parser.parse_args()

    if not args.local_json and not os.environ.get("FUELARK_API_KEY"):
        print("WARNING: FUELARK_API_KEY is not set. Use --local-json or export the key.", file=sys.stderr)

    if args.local_json:
        df = load_local_json(args.local_json, imei=args.imei)
        import pandas as pd
        import config

        start = pd.Timestamp(args.from_dt)
        end = pd.Timestamp(args.to_dt)
        if start.tzinfo is None:
            start = start.tz_localize(config.TIMEZONE)
        if end.tzinfo is None:
            if len(str(args.to_dt)) <= 10:
                end = end + pd.Timedelta(days=1) - pd.Timedelta(seconds=1)
            end = end.tz_localize(config.TIMEZONE)
        df = df[(df["timestamp"] >= start) & (df["timestamp"] <= end)].copy()
    else:
        df = fetch_range(args.imei, args.from_dt, args.to_dt, use_cache=not args.no_cache)

    if df.empty:
        print("No telemetry in this window.")
        return

    df = attach_vehicle_names(df)
    _, results = run_session_pipeline(df, assignment_path=args.assignment_history)

    if results.empty:
        print("No drain/fill sessions in this window.")
        return

    cols = [
        c
        for c in [
            "start_time",
            "end_time",
            "type",
            "magnitude",
            "duration_minutes",
            "num_ticks",
            "tamper_flag",
            "confidence",
            "decision",
            "vehicle",
        ]
        if c in results.columns
    ]
    print(results[cols].to_string(index=False))


if __name__ == "__main__":
    main()
