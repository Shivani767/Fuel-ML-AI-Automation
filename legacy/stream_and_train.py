"""Fetch live streaming data and train the fuel event model."""

import argparse
import subprocess
import sys

from fetch_live_data import fetch_all_vehicles, save_live_data

JUNE_DATA_PATH = "fuel_data/june_2026_data.json"


def main():
    parser = argparse.ArgumentParser(description="Fetch data and train ML model")
    parser.add_argument("--days", type=int, default=7, help="Days of live history to fetch")
    parser.add_argument("--start", default=None, help="Start date YYYY-MM-DD")
    parser.add_argument("--end", default=None, help="End date YYYY-MM-DD")
    parser.add_argument("--skip-fetch", action="store_true", help="Train on existing data file")
    parser.add_argument("--data", default=None, help="Explicit data file for training")
    args = parser.parse_args()

    data_path = args.data

    if not args.skip_fetch:
        from datetime import datetime
        start = datetime.strptime(args.start, "%Y-%m-%d") if args.start else None
        end = datetime.strptime(args.end, "%Y-%m-%d") if args.end else None
        output = JUNE_DATA_PATH if start and end else "fuel_data/live_fuel_data.json"
        print("Step 1: Fetching fuel data...")
        data = fetch_all_vehicles(lookback_days=args.days, start=start, end=end)
        if not data:
            print("Failed to fetch data. Exiting.")
            sys.exit(1)
        save_live_data(data, output)
        data_path = output
    else:
        print("Step 1: Skipped fetch, using existing data file.")

    print("\nStep 2: Training model...")
    cmd = [sys.executable, "train_model.py"]
    if data_path:
        cmd.extend(["--data", data_path])
    result = subprocess.run(cmd, check=False)
    sys.exit(result.returncode)


if __name__ == "__main__":
    main()
