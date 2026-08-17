"""Fetch live streaming fuel telemetry from FuelArk API."""

import argparse
import json
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

from fetch_100_vehicles import IMEIS
from fuel_api import fetch_imei_dates, fetch_imei_range, login_and_get_token, trim_point

LIVE_DATA_PATH = "fuel_data/live_fuel_data.json"
DEVICE_IMEI_MAP_PATH = "fuel_data/device_imei_map.json"


def stamp_points(points, imei):
    stamped = []
    for point in points:
        trimmed = trim_point(point)
        trimmed["imei"] = trimmed.get("imei") or imei
        stamped.append(trimmed)
    return stamped


def load_device_imei_map(path=DEVICE_IMEI_MAP_PATH):
    if not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_device_imei_map(mapping, path=DEVICE_IMEI_MAP_PATH):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(mapping, f, ensure_ascii=False, indent=2)


def update_device_imei_map(points, path=DEVICE_IMEI_MAP_PATH):
    mapping = load_device_imei_map(path)
    changed = False
    for point in points:
        name = point.get("deviceName")
        imei = point.get("imei")
        if name and imei and mapping.get(name) != imei:
            mapping[name] = str(imei)
            changed = True
    if changed:
        save_device_imei_map(mapping, path)
    return mapping


def fetch_vehicle(imei, lookback_days, token, start=None, end=None):
    if start and end:
        points = fetch_imei_dates(imei, start, end, token=token)
    else:
        points = fetch_imei_range(imei, lookback_days=lookback_days, token=token)
    return imei, stamp_points(points, imei)


def fetch_all_vehicles(lookback_days=7, imeis=None, workers=8, start=None, end=None):
    imeis = imeis or IMEIS
    token = login_and_get_token()
    auth_mode = "bearer" if token else "api-key"
    if not token:
        print("Bearer login failed, falling back to API key auth.")

    all_data = []
    completed = 0
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {
            executor.submit(fetch_vehicle, imei, lookback_days, token, start, end): imei
            for imei in imeis
        }
        for future in as_completed(futures):
            imei, points = future.result()
            completed += 1
            if points:
                all_data.extend(points)
                print(f"[{completed}/{len(imeis)}] IMEI {imei}: {len(points)} points", flush=True)
            else:
                print(f"[{completed}/{len(imeis)}] IMEI {imei}: no data", flush=True)

    if all_data:
        update_device_imei_map(all_data)
    print(f"Fetched {len(all_data)} total points using {auth_mode} auth")
    return all_data


def save_live_data(data, path=LIVE_DATA_PATH):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    print(f"Saved {len(data)} live data points to {path}")


def stream_loop(interval_seconds=300, lookback_days=1, max_iterations=None):
    iteration = 0
    while max_iterations is None or iteration < max_iterations:
        iteration += 1
        print(f"\n=== Live stream fetch iteration {iteration} ===")
        data = fetch_all_vehicles(lookback_days=lookback_days)
        if data:
            save_live_data(data)
        else:
            print("No live data received.")
        if max_iterations is not None and iteration >= max_iterations:
            break
        print(f"Waiting {interval_seconds}s before next fetch...")
        time.sleep(interval_seconds)


def main():
    parser = argparse.ArgumentParser(description="Fetch live fuel telemetry from FuelArk")
    parser.add_argument("--days", type=int, default=7, help="Days of live history to fetch")
    parser.add_argument("--stream", action="store_true", help="Continuously poll for live data")
    parser.add_argument("--interval", type=int, default=300, help="Seconds between stream polls")
    parser.add_argument("--iterations", type=int, default=None, help="Max stream iterations")
    parser.add_argument("--workers", type=int, default=8, help="Parallel fetch workers")
    parser.add_argument("--output", default=LIVE_DATA_PATH, help="Output JSON path")
    parser.add_argument("--start", default=None, help="Start date YYYY-MM-DD (overrides --days)")
    parser.add_argument("--end", default=None, help="End date YYYY-MM-DD (overrides --days)")
    args = parser.parse_args()

    start = datetime.strptime(args.start, "%Y-%m-%d") if args.start else None
    end = datetime.strptime(args.end, "%Y-%m-%d") if args.end else None

    if args.stream:
        stream_loop(args.interval, args.days, args.iterations)
        return

    data = fetch_all_vehicles(
        lookback_days=args.days, workers=args.workers, start=start, end=end
    )
    if not data:
        print("No live data fetched.")
        return
    save_live_data(data, args.output)


if __name__ == "__main__":
    main()
