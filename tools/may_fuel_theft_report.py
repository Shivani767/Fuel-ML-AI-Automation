#!/usr/bin/env python3
"""Fetch May telemetry for fleet IMEIs and export a fuel-theft report CSV.

Usage:
  python3 tools/may_fuel_theft_report.py              # fetch May + report
  python3 tools/may_fuel_theft_report.py --skip-fetch # use existing may JSON
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "fuel-theft-detector"))

from env import load_env  # noqa: E402

# FuelArk credentials come from .env (gitignored), not a hardcoded fallback.
load_env()


def load_imeis(path: Path) -> list[str]:
    return [
        ln.strip()
        for ln in path.read_text(encoding="utf-8").splitlines()
        if ln.strip() and not ln.strip().startswith("#")
    ]


def fetch_may(imeis: list[str], out_json: Path, workers: int) -> list:
    from fetch_live_data import fetch_all_vehicles, save_live_data, update_device_imei_map

    start = datetime(2026, 5, 1)
    end = datetime(2026, 5, 31)
    print(f"Fetching {len(imeis)} IMEIs for {start.date()} → {end.date()} …", flush=True)
    data = fetch_all_vehicles(imeis=imeis, workers=workers, start=start, end=end)
    if not data:
        print("No May data returned from API.", flush=True)
        return []
    save_live_data(data, str(out_json))
    update_device_imei_map(data)
    print(f"Saved {len(data)} points → {out_json}", flush=True)
    return data


def points_by_device(raw: list) -> dict[str, list]:
    by: dict[str, list] = defaultdict(list)
    for r in raw:
        name = (r.get("deviceName") or "").strip()
        if not name:
            # keep under IMEI bucket if name missing
            name = str(r.get("imei") or "UNKNOWN")
        # normalize time field for detector
        if "time" not in r and r.get("deviceDataInsertTime"):
            r = dict(r)
            r["time"] = r["deviceDataInsertTime"]
        by[name].append(r)
    for name in by:
        by[name].sort(key=lambda x: str(x.get("time") or ""))
    return by


def run_theft_report(by_device: dict[str, list], out_csv: Path, theft_min: float = 2.0) -> list[dict]:
    from backend.services.detection import detect_fill_theft

    rows = []
    devices = sorted(by_device.keys())
    print(f"Running theft detection on {len(devices)} devices…", flush=True)
    for i, name in enumerate(devices, 1):
        pts = by_device[name]
        if len(pts) < 5:
            print(f"  [{i}/{len(devices)}] {name}: skip ({len(pts)} pts)", flush=True)
            continue
        try:
            events = detect_fill_theft(name, pts, theft_min=theft_min, fill_min=2.0)
        except Exception as exc:
            print(f"  [{i}/{len(devices)}] {name}: ERROR {exc}", flush=True)
            continue
        thefts = [e for e in events if e.get("eventType") == "Fuel Theft"]
        imei = pts[0].get("imei") or ""
        print(
            f"  [{i}/{len(devices)}] {name}: {len(pts)} pts → {len(thefts)} thefts",
            flush=True,
        )
        for e in thefts:
            rows.append(
                {
                    "deviceName": name,
                    "imei": imei,
                    "eventTime": e.get("eventTime"),
                    "endTime": e.get("endTime") or "",
                    "volume_L": e.get("volume"),
                    "rawVolume_L": e.get("rawVolume") or e.get("volume"),
                    "decision": e.get("decision"),
                    "confidence": e.get("confidence"),
                    "source": e.get("source"),
                    "ignition": e.get("ignition") or "",
                    "trulyParked": e.get("trulyParked"),
                    "tamper": e.get("tamper"),
                    "durationMinutes": e.get("durationMinutes") or "",
                    "latitude": e.get("latitude") or "",
                    "longitude": e.get("longitude") or "",
                }
            )

    out_csv.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "deviceName",
        "imei",
        "eventTime",
        "endTime",
        "volume_L",
        "rawVolume_L",
        "decision",
        "confidence",
        "source",
        "ignition",
        "trulyParked",
        "tamper",
        "durationMinutes",
        "latitude",
        "longitude",
    ]
    with open(out_csv, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(sorted(rows, key=lambda r: (str(r["deviceName"]), str(r["eventTime"]))))

    # Summary by vehicle
    summary_path = out_csv.with_name(out_csv.stem + "_summary_by_vehicle.csv")
    by_veh: dict[str, list] = defaultdict(list)
    for r in rows:
        by_veh[r["deviceName"]].append(r)
    with open(summary_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(
            f,
            fieldnames=[
                "deviceName",
                "imei",
                "theftCount",
                "totalTheft_L",
                "autoConfirmed",
                "manualReview",
                "verified",
            ],
        )
        w.writeheader()
        for name in sorted(by_veh):
            evs = by_veh[name]
            w.writerow(
                {
                    "deviceName": name,
                    "imei": evs[0]["imei"],
                    "theftCount": len(evs),
                    "totalTheft_L": round(sum(float(e["volume_L"] or 0) for e in evs), 2),
                    "autoConfirmed": sum(1 for e in evs if e["decision"] == "AUTO_CONFIRMED"),
                    "manualReview": sum(1 for e in evs if e["decision"] == "MANUAL_REVIEW"),
                    "verified": sum(1 for e in evs if e["decision"] == "VERIFIED"),
                }
            )

    print(f"\nWrote {len(rows)} theft events → {out_csv}", flush=True)
    print(f"Wrote vehicle summary → {summary_path}", flush=True)
    return rows


def main():
    parser = argparse.ArgumentParser(description="May 2026 fuel theft report for fleet IMEIs")
    parser.add_argument(
        "--imeis",
        default=str(ROOT / "fuel_data" / "fleet_imeis.txt"),
    )
    parser.add_argument(
        "--data",
        default=str(ROOT / "fuel_data" / "may_2026_data.json"),
        help="May telemetry JSON (created by fetch)",
    )
    parser.add_argument(
        "--out",
        default=str(ROOT / "fuel-theft-detector" / "output" / "reports" / "may_2026_fuel_theft.csv"),
    )
    parser.add_argument("--workers", type=int, default=10)
    parser.add_argument("--skip-fetch", action="store_true")
    parser.add_argument("--theft-min", type=float, default=2.0)
    args = parser.parse_args()

    data_path = Path(args.data)
    imeis = load_imeis(Path(args.imeis))
    print(f"Fleet list: {len(imeis)} IMEIs", flush=True)

    if args.skip_fetch:
        if not data_path.exists():
            raise SystemExit(f"No data at {data_path}. Run without --skip-fetch first.")
        print(f"Loading existing {data_path} …", flush=True)
        with open(data_path, "r", encoding="utf-8") as f:
            raw = json.load(f)
    else:
        raw = fetch_may(imeis, data_path, workers=args.workers)
        if not raw:
            raise SystemExit(1)

    print(f"Points loaded: {len(raw)}", flush=True)
    by = points_by_device(raw)
    print(f"Devices with data: {len(by)}", flush=True)
    run_theft_report(by, Path(args.out), theft_min=args.theft_min)


if __name__ == "__main__":
    main()
