#!/usr/bin/env python3
"""Nightly full-fleet telemetry capture.

Why this exists
---------------
The FuelArk API is not a historical archive. Probing it showed recent days return
full ~20-30 s data (3,500-4,400 records/device/day) while 2026-06-12, 06-14 and
07-15 return **zero** and 2026-06-01 returns a partial ~660. History cannot be
re-fetched, so anything not captured today is lost permanently.

Compounding that, everything persisted so far went through a 14-field trim while the
API returns 55. The discarded fields included the odometer (iO_16) and GPS fix quality
(satellites), both of which materially affect detection accuracy.

So: capture the **full payload**, every device, every night.

Usage:
    python3 tools/nightly_capture.py                 # yesterday, whole fleet
    python3 tools/nightly_capture.py --date 2026-07-28
    python3 tools/nightly_capture.py --days 7        # backfill last 7 days
"""

from __future__ import annotations

import argparse
import csv
import gzip
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from env import load_env, require  # noqa: E402

load_env()

OUT_DIR = ROOT / "fuel_data" / "daily"
COVERAGE_CSV = OUT_DIR / "coverage.csv"
IMEI_FILE = ROOT / "fuel_data" / "fleet_imeis.txt"
IMEI_MAP = ROOT / "fuel_data" / "device_imei_map.json"

COVERAGE_FIELDS = [
    "date", "imei", "device_name", "n_records", "first_ts", "last_ts",
    "span_hours", "median_gap_s", "longest_gap_min", "coverage_pct",
    "moving_ticks", "ignition_on_ticks", "ignition_trustworthy",
    "has_odometer", "odometer_km", "fuel_min", "fuel_max", "fetched_at",
]


def load_imeis() -> list:
    """Fleet IMEI list, preferring the explicit file and falling back to the map."""
    if IMEI_FILE.exists():
        out = [
            ln.strip()
            for ln in IMEI_FILE.read_text(encoding="utf-8").splitlines()
            if ln.strip() and not ln.strip().startswith("#")
        ]
        if out:
            return sorted(set(out))
    if IMEI_MAP.exists():
        return sorted({str(v).strip() for v in json.loads(IMEI_MAP.read_text()).values()})
    raise SystemExit(f"No IMEI source found ({IMEI_FILE} or {IMEI_MAP})")


def fetch_day(imei: str, day: str, api_key: str, api_url: str, retries: int = 3) -> list:
    """One IMEI-day. Returns the raw records, untrimmed."""
    headers = {"x-api-key": api_key, "Content-Type": "application/json"}
    params = {"fromdate": f"{day} 00:00:00", "todate": f"{day} 23:59:59", "IMEI": imei}
    last = None
    for attempt in range(retries):
        try:
            r = requests.get(api_url, headers=headers, params=params, timeout=120)
            r.raise_for_status()
            j = r.json()
            rd = j.get("responseData") or j.get("ResponseData") or {}
            return rd.get("deviceFuelInfo") or rd.get("DeviceFuelInfo") or []
        except (requests.RequestException, ValueError) as e:
            last = e
            if attempt < retries - 1:
                time.sleep(1.5 * (2 ** attempt))
    raise RuntimeError(f"{imei} {day}: {last}")


def _f(rec: dict, key: str):
    v = rec.get(key)
    if v in (None, ""):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def summarize(imei: str, day: str, records: list) -> dict:
    """Coverage row. This is what separates 'no theft' from 'no data'.

    A device that reported only 00:00-05:29 looks identical to a quiet one unless
    coverage is recorded -- that ambiguity made a known theft score as a detector miss.
    """
    row = {k: "" for k in COVERAGE_FIELDS}
    row.update({
        "date": day, "imei": imei, "n_records": len(records),
        "fetched_at": datetime.now().isoformat(timespec="seconds"),
    })
    if not records:
        row["coverage_pct"] = 0.0
        return row

    ts = sorted(
        datetime.fromisoformat(str(r.get("deviceDataInsertTime"))[:19])
        for r in records
        if r.get("deviceDataInsertTime")
    )
    gaps = [(ts[i + 1] - ts[i]).total_seconds() for i in range(len(ts) - 1)]
    gaps_sorted = sorted(gaps)
    span_h = (ts[-1] - ts[0]).total_seconds() / 3600.0 if len(ts) > 1 else 0.0

    speeds = [_f(r, "speed") or 0.0 for r in records]
    moving = sum(1 for s in speeds if s > 3)
    ign_on = sum(1 for r in records if (_f(r, "iO_1") or 0) == 1)
    odo = [v for v in (_f(r, "iO_16") for r in records) if v is not None]
    fuel = [v for v in (_f(r, "fuelInLiter") for r in records) if v is not None]

    row.update({
        "device_name": records[0].get("deviceName") or "",
        "first_ts": ts[0].isoformat(),
        "last_ts": ts[-1].isoformat(),
        "span_hours": round(span_h, 2),
        "median_gap_s": round(gaps_sorted[len(gaps_sorted) // 2], 1) if gaps_sorted else "",
        "longest_gap_min": round(max(gaps) / 60.0, 1) if gaps else "",
        # Fraction of the day actually represented, using the device's own cadence.
        "coverage_pct": round(100.0 * span_h / 24.0, 1),
        "moving_ticks": moving,
        "ignition_on_ticks": ign_on,
        # Drives but never reports ignition ON => iO_1 is broken/unwired on this device.
        "ignition_trustworthy": not (moving >= 20 and ign_on == 0),
        "has_odometer": bool(odo),
        "odometer_km": round((max(odo) - min(odo)) / 1000.0, 2) if odo else "",
        "fuel_min": round(min(fuel), 2) if fuel else "",
        "fuel_max": round(max(fuel), 2) if fuel else "",
    })
    return row


def append_coverage(rows: list) -> None:
    COVERAGE_CSV.parent.mkdir(parents=True, exist_ok=True)
    new = not COVERAGE_CSV.exists()
    with COVERAGE_CSV.open("a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=COVERAGE_FIELDS)
        if new:
            w.writeheader()
        w.writerows(rows)


def capture_day(day: str, imeis: list, api_key: str, api_url: str,
                workers: int, force: bool) -> list:
    day_dir = OUT_DIR / day
    day_dir.mkdir(parents=True, exist_ok=True)
    rows = []

    def one(imei: str):
        # Full payloads compress ~97% (5.0 MB -> 0.1 MB), turning a ~93 GB/year
        # archive into ~3 GB. Plain .json is still read for anything already captured.
        gz_path = day_dir / f"{imei}.json.gz"
        raw_path = day_dir / f"{imei}.json"
        if not force:
            for path, opener in ((gz_path, gzip.open), (raw_path, open)):
                if path.exists():
                    try:
                        with opener(path, "rt", encoding="utf-8") as f:
                            return imei, json.load(f), True
                    except (OSError, ValueError):
                        break  # corrupt -> re-fetch
        recs = fetch_day(imei, day, api_key, api_url)
        with gzip.open(gz_path, "wt", encoding="utf-8") as f:
            json.dump(recs, f, ensure_ascii=False)
        if raw_path.exists():
            raw_path.unlink()  # supersede any uncompressed copy
        return imei, recs, False

    with ThreadPoolExecutor(max_workers=workers) as ex:
        futures = {ex.submit(one, i): i for i in imeis}
        done = 0
        for fut in as_completed(futures):
            imei = futures[fut]
            done += 1
            try:
                imei, recs, cached = fut.result()
                rows.append(summarize(imei, day, recs))
                tag = "cached" if cached else "fetched"
                print(f"  [{done:3d}/{len(imeis)}] {imei} {len(recs):5d} recs ({tag})")
            except Exception as e:
                rows.append(summarize(imei, day, []))
                print(f"  [{done:3d}/{len(imeis)}] {imei} ERROR: {e}")
    return rows


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--date", help="YYYY-MM-DD (default: yesterday)")
    ap.add_argument("--days", type=int, default=1, help="Capture N days ending at --date")
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--force", action="store_true", help="Re-fetch even if the file exists")
    ap.add_argument("--limit", type=int, help="Only the first N IMEIs (smoke test)")
    args = ap.parse_args()

    api_key = require("FUELARK_API_KEY")
    api_url = os.environ.get(
        "FUELARK_API_URL",
        "https://api1.fuelark.com/api/FuelConsumption/DeviceFuelConsumption",
    )

    end = datetime.strptime(args.date, "%Y-%m-%d").date() if args.date else date.today() - timedelta(days=1)
    imeis = load_imeis()
    if args.limit:
        imeis = imeis[: args.limit]

    print(f"Fleet: {len(imeis)} IMEIs | {args.days} day(s) ending {end} | {args.workers} workers")
    all_rows = []
    for i in range(args.days):
        day = (end - timedelta(days=args.days - 1 - i)).isoformat()
        print(f"\n=== {day} ===")
        all_rows += capture_day(day, imeis, api_key, api_url, args.workers, args.force)

    append_coverage(all_rows)

    with_data = [r for r in all_rows if r["n_records"]]
    untrusted = [r for r in all_rows if r["ignition_trustworthy"] is False]
    no_odo = [r for r in with_data if not r["has_odometer"]]
    print(f"\n==== SUMMARY ====")
    print(f"  IMEI-days captured : {len(all_rows)}")
    print(f"  with data          : {len(with_data)}")
    print(f"  empty (no report)  : {len(all_rows) - len(with_data)}")
    print(f"  broken ignition    : {len(untrusted)}")
    if untrusted:
        for r in untrusted[:10]:
            print(f"      {r['imei']}  {r['device_name'][:34]:34s} moving={r['moving_ticks']} ign_on=0")
    print(f"  missing odometer   : {len(no_odo)}")
    print(f"\n  coverage log -> {COVERAGE_CSV}")
    print(f"  raw payloads -> {OUT_DIR}/<date>/<imei>.json")


if __name__ == "__main__":
    main()
