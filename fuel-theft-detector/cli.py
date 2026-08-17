#!/usr/bin/env python3
"""CLI entrypoint for the Fuel Theft & Fill Detection System."""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pandas as pd

import config
from data.fetch import fetch_range, fetch_batch, load_local_json
from data.vehicle_assignments import attach_vehicle_names, load_assignments
from detection.scoring import run_detection
from reports.build_report import build_event_table, compute_day_rollups, export_reports
from viz.plot_events import plot_events


def _parse_args():
    parser = argparse.ArgumentParser(
        description="Fuel theft & fill detector (batch CLI)"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="Fetch + detect + plot + report")
    run.add_argument("--imei", help="Single IMEI")
    run.add_argument("--batch", help="CSV with an 'imei' column")
    run.add_argument("--from", dest="date_from", required=True, help='e.g. "2026-06-28"')
    run.add_argument("--to", dest="date_to", required=True)
    run.add_argument("--local-json", help="Skip API; load FuelArk JSON dump")
    run.add_argument("--no-cache", action="store_true")
    run.add_argument("--no-plot", action="store_true")
    run.add_argument("--vehicle-type", default=None, help="Override vehicle type burn rates")

    plot = sub.add_parser("plot", help="Fetch/load + detect + plot only")
    plot.add_argument("--imei", required=True)
    plot.add_argument("--from", dest="date_from", required=True)
    plot.add_argument("--to", dest="date_to", required=True)
    plot.add_argument("--local-json", help="Skip API; load FuelArk JSON dump")
    plot.add_argument("--no-cache", action="store_true")

    report = sub.add_parser("report", help="Generate report from prior run cache / local JSON")
    report.add_argument("--imei", help="Single IMEI")
    report.add_argument("--batch", help="CSV with an 'imei' column")
    report.add_argument("--from", dest="date_from", required=True)
    report.add_argument("--to", dest="date_to", required=True)
    report.add_argument("--local-json", help="Skip API; load FuelArk JSON dump")
    report.add_argument("--no-cache", action="store_true")

    return parser.parse_args()


def _load_imeis(args) -> list:
    if getattr(args, "imei", None):
        return [str(args.imei).strip()]
    if getattr(args, "batch", None):
        df = pd.read_csv(args.batch, dtype=str)
        col = "imei" if "imei" in df.columns else df.columns[0]
        return [str(x).strip() for x in df[col].dropna().tolist() if str(x).strip()]
    raise SystemExit("Provide --imei or --batch")


def _load_telemetry(args) -> pd.DataFrame:
    use_cache = not getattr(args, "no_cache", False)
    if getattr(args, "local_json", None):
        imeis = None
        try:
            imeis = _load_imeis(args)
        except SystemExit:
            imeis = None
        if imeis and len(imeis) == 1:
            df = load_local_json(args.local_json, imei=imeis[0])
        else:
            df = load_local_json(args.local_json)
            if imeis:
                df = df[df["imei"].astype(str).isin(imeis)]
        # Filter date range
        start = pd.Timestamp(args.date_from).tz_localize(config.TIMEZONE)
        end = pd.Timestamp(args.date_to)
        if end.tzinfo is None:
            if len(str(args.date_to)) <= 10:
                end = end + pd.Timedelta(days=1) - pd.Timedelta(seconds=1)
            end = end.tz_localize(config.TIMEZONE)
        df = df[(df["timestamp"] >= start) & (df["timestamp"] <= end)].copy()
        return df.reset_index(drop=True)

    imeis = _load_imeis(args)
    if len(imeis) == 1:
        return fetch_range(imeis[0], args.date_from, args.date_to, use_cache=use_cache)
    return fetch_batch(imeis, args.date_from, args.date_to, use_cache=use_cache)


def _prepare(df: pd.DataFrame, vehicle_type_override=None) -> pd.DataFrame:
    df = attach_vehicle_names(df, load_assignments())
    if vehicle_type_override:
        df["vehicle_type"] = vehicle_type_override
    return df


def _stem(args) -> str:
    imei = getattr(args, "imei", None) or "batch"
    return f"{imei}_{args.date_from}_{args.date_to}".replace(" ", "_").replace(":", "")


def cmd_run(args):
    raw = _load_telemetry(args)
    if raw.empty:
        raise SystemExit("No telemetry returned for the requested range.")
    telemetry = _prepare(raw, getattr(args, "vehicle_type", None))
    print(f"[run] {len(telemetry)} points across {telemetry['imei'].nunique()} IMEI(s)")

    all_prepared = []
    all_events = []
    for imei, group in telemetry.groupby("imei"):
        prepared, events = run_detection(group)
        all_prepared.append(prepared)
        if not events.empty:
            all_events.append(events)
        print(
            f"[run] {imei}: {len(events)} scored events "
            f"(auto={int((events['decision']=='AUTO_CONFIRMED').sum()) if not events.empty else 0}, "
            f"review={int((events['decision']=='MANUAL_REVIEW').sum()) if not events.empty else 0})"
        )
        if not args.no_plot:
            plot_events(prepared, events)

    prepared = pd.concat(all_prepared, ignore_index=True)
    events = (
        pd.concat(all_events, ignore_index=True)
        if all_events
        else pd.DataFrame()
    )
    rollups = compute_day_rollups(prepared, events)
    export_reports(events, rollups, stem=_stem(args))
    if not events.empty:
        print(build_event_table(events).to_string(index=False))
    else:
        print("[run] No events above noise floor.")


def cmd_plot(args):
    raw = _load_telemetry(args)
    if raw.empty:
        raise SystemExit("No telemetry to plot.")
    telemetry = _prepare(raw)
    for imei, group in telemetry.groupby("imei"):
        prepared, events = run_detection(group)
        plot_events(prepared, events)


def cmd_report(args):
    raw = _load_telemetry(args)
    if raw.empty:
        raise SystemExit("No telemetry for report.")
    telemetry = _prepare(raw)
    all_prepared, all_events = [], []
    for _, group in telemetry.groupby("imei"):
        prepared, events = run_detection(group)
        all_prepared.append(prepared)
        if not events.empty:
            all_events.append(events)
    prepared = pd.concat(all_prepared, ignore_index=True)
    events = pd.concat(all_events, ignore_index=True) if all_events else pd.DataFrame()
    rollups = compute_day_rollups(prepared, events)
    export_reports(events, rollups, stem=_stem(args))


def main():
    args = _parse_args()
    if args.command == "run":
        cmd_run(args)
    elif args.command == "plot":
        cmd_plot(args)
    elif args.command == "report":
        cmd_report(args)
    else:
        raise SystemExit(f"Unknown command: {args.command}")


if __name__ == "__main__":
    main()
