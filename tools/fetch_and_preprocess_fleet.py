#!/usr/bin/env python3
"""Fetch fleet IMEIs from FuelArk, stamp imei, save JSON, and normalize to parquet."""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "fuel-theft-detector"))

from fetch_live_data import (  # noqa: E402
    DEVICE_IMEI_MAP_PATH,
    fetch_all_vehicles,
    save_device_imei_map,
    save_live_data,
    update_device_imei_map,
)
from data.fetch import normalize_records  # noqa: E402


def load_imeis(path: Path) -> list[str]:
    lines = path.read_text(encoding="utf-8").splitlines()
    return [ln.strip() for ln in lines if ln.strip() and not ln.strip().startswith("#")]


def preprocess_to_parquet(raw_points: list, out_parquet: Path) -> int:
    by_imei: dict[str, list] = {}
    for row in raw_points:
        imei = str(row.get("imei") or "").strip()
        if not imei:
            continue
        by_imei.setdefault(imei, []).append(row)

    frames = []
    for imei, rows in by_imei.items():
        df = normalize_records(rows, imei=imei)
        if not df.empty:
            frames.append(df)

    if not frames:
        print("Preprocess: no rows with IMEI to normalize.")
        return 0

    import pandas as pd

    out = pd.concat(frames, ignore_index=True).sort_values(["imei", "timestamp"])
    out_parquet.parent.mkdir(parents=True, exist_ok=True)
    try:
        out.to_parquet(out_parquet, index=False)
    except Exception:
        csv_path = out_parquet.with_suffix(".csv")
        out.to_csv(csv_path, index=False)
        print(f"Parquet unavailable; wrote CSV instead: {csv_path}")
        return len(out)

    print(f"Preprocessed {len(out)} rows → {out_parquet}")
    print(f"  IMEIs: {out['imei'].nunique()}  vehicles: {out['vehicle_name_raw'].nunique()}")
    return len(out)


def main():
    parser = argparse.ArgumentParser(description="Fetch + preprocess fleet IMEIs")
    parser.add_argument(
        "--imeis",
        default=str(ROOT / "fuel_data" / "fleet_imeis.txt"),
        help="Text file with one IMEI per line",
    )
    parser.add_argument("--start", default="2026-06-01")
    parser.add_argument("--end", default="2026-06-30")
    parser.add_argument(
        "--output-json",
        default=str(ROOT / "fuel_data" / "june_2026_imei_data.json"),
    )
    parser.add_argument(
        "--output-parquet",
        default=str(ROOT / "fuel_data" / "june_2026_normalized.parquet"),
    )
    parser.add_argument("--workers", type=int, default=10)
    parser.add_argument(
        "--promote",
        action="store_true",
        help="Also overwrite fuel_data/june_2026_data.json with stamped data",
    )
    args = parser.parse_args()

    imeis = load_imeis(Path(args.imeis))
    print(f"Loaded {len(imeis)} IMEIs from {args.imeis}")
    print(f"Fetching {args.start} → {args.end} with {args.workers} workers...")

    start = datetime.strptime(args.start, "%Y-%m-%d")
    end = datetime.strptime(args.end, "%Y-%m-%d")
    data = fetch_all_vehicles(
        imeis=imeis, workers=args.workers, start=start, end=end
    )
    if not data:
        print("No data fetched.")
        sys.exit(1)

    save_live_data(data, args.output_json)
    mapping = update_device_imei_map(data, DEVICE_IMEI_MAP_PATH)
    print(f"device_imei_map entries: {len(mapping)}")

    n = preprocess_to_parquet(data, Path(args.output_parquet))
    if args.promote:
        promote_path = ROOT / "fuel_data" / "june_2026_data.json"
        save_live_data(data, str(promote_path))
        print(f"Promoted stamped JSON → {promote_path}")

    print(f"Done. points={len(data)} normalized_rows={n}")


if __name__ == "__main__":
    main()
