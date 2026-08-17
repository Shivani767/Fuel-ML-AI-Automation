"""Full automated pipeline: fetch June data -> analyze -> train -> ML alerts -> dashboard.

Usage:
    python3 automate.py fetch          # fetch June 2026 data for 100 vehicles
    python3 automate.py train          # train model on June data
    python3 automate.py analyze          # rule-based event detection
    python3 automate.py ml             # run ML monitor / generate alerts
    python3 automate.py all            # run everything
    python3 automate.py dashboard      # start Flask dashboard
    python3 automate.py watch          # continuous live ML monitoring
"""

import argparse
import subprocess
import sys

JUNE_START = "2026-06-01"
JUNE_END = "2026-06-30"
JUNE_DATA = "fuel_data/june_2026_data.json"


def run(cmd):
    print(f"\n>>> {' '.join(cmd)}")
    result = subprocess.run(cmd, check=False)
    if result.returncode != 0:
        print(f"Command failed with exit code {result.returncode}")
        sys.exit(result.returncode)


def fetch_june():
    run([
        sys.executable, "fetch_live_data.py",
        "--start", JUNE_START, "--end", JUNE_END,
        "--workers", "10", "--output", JUNE_DATA,
    ])


def train():
    run([sys.executable, "legacy/train_model.py", "--data", JUNE_DATA])


def analyze():
    run([sys.executable, "legacy/analyze_fuel_data.py"])


def ml_alerts():
    run([sys.executable, "legacy/ml_monitor.py", "--data", JUNE_DATA])


def dashboard():
    run([sys.executable, "app.py"])


def watch():
    run([sys.executable, "legacy/ml_monitor.py", "--watch", "--interval", "300"])


def main():
    parser = argparse.ArgumentParser(description="Fuel ML/AI automation pipeline")
    parser.add_argument(
        "action",
        choices=["fetch", "train", "analyze", "ml", "all", "dashboard", "watch"],
        help="Pipeline step to run",
    )
    args = parser.parse_args()

    if args.action == "fetch":
        fetch_june()
    elif args.action == "train":
        train()
    elif args.action == "analyze":
        analyze()
    elif args.action == "ml":
        ml_alerts()
    elif args.action == "all":
        fetch_june()
        analyze()
        train()
        ml_alerts()
        print("\nPipeline complete! Start dashboard with: python3 automate.py dashboard")
    elif args.action == "dashboard":
        dashboard()
    elif args.action == "watch":
        watch()


if __name__ == "__main__":
    main()
