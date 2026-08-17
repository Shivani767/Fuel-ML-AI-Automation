#!/usr/bin/env python3
"""Continuous telemetry capture: heals gaps in history, then keeps pulling new data.

Why a daemon and not a nightly cron
-----------------------------------
FuelArk serves history unreliably. Probing on 2026-08-02 found data for
Jun 21 - Jul 11 and Jul 26 - Aug 02, but Jul 12-25 returns zero for every device
on every day -- a permanent hole. A once-a-night job that misses its window loses
that day for good, and a laptop that was asleep never notices.

So each cycle this:
  1. re-fetches today and yesterday, which are still accumulating points
  2. fills any day inside the lookback window that is missing or incomplete
  3. retries previously-empty days with backoff, in case they land late
  4. sleeps and repeats

Every step is idempotent -- already-captured IMEI-days are read from disk, not
re-fetched -- so restarting costs nothing and interruption is safe.

Usage:
    python3 tools/capture_daemon.py                    # 15-min cycle, 45-day lookback
    python3 tools/capture_daemon.py --once             # single pass, then exit
    python3 tools/capture_daemon.py --interval 600 --lookback 60
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from env import load_env, require  # noqa: E402
from tools.nightly_capture import (  # noqa: E402
    OUT_DIR,
    append_coverage,
    capture_day,
    load_imeis,
)

load_env()

MANIFEST = OUT_DIR / "manifest.json"

# Today and yesterday keep growing, so never treat them as finished.
ALWAYS_REFETCH_DAYS = 2

# A day where every device returned nothing is usually a real hole in FuelArk
# (Jul 12-25 was), but occasionally data lands late. Retry a few times, then stop
# burning ~104 calls per cycle on it.
MAX_EMPTY_ATTEMPTS = 4


def load_manifest() -> dict:
    if MANIFEST.exists():
        try:
            return json.loads(MANIFEST.read_text())
        except (OSError, ValueError):
            pass
    return {}


def save_manifest(m: dict) -> None:
    MANIFEST.parent.mkdir(parents=True, exist_ok=True)
    MANIFEST.write_text(json.dumps(m, indent=1, sort_keys=True), encoding="utf-8")


def day_state(day: str, imeis: list, manifest: dict) -> str:
    """One of: complete | partial | empty_exhausted | pending."""
    entry = manifest.get(day) or {}
    day_dir = OUT_DIR / day
    have = len(list(day_dir.glob("*.json.gz"))) + len(list(day_dir.glob("*.json"))) if day_dir.exists() else 0

    if entry.get("status") == "empty" and entry.get("attempts", 0) >= MAX_EMPTY_ATTEMPTS:
        return "empty_exhausted"
    if have >= len(imeis):
        return "complete"
    if have > 0:
        return "partial"
    return "pending"


def cycle(imeis: list, lookback: int, workers: int) -> dict:
    manifest = load_manifest()
    today = date.today()
    api_key = require("FUELARK_API_KEY")
    import os

    api_url = os.environ.get(
        "FUELARK_API_URL",
        "https://api1.fuelark.com/api/FuelConsumption/DeviceFuelConsumption",
    )

    targets = []
    for back in range(lookback):
        d = today - timedelta(days=back)
        ds = d.isoformat()
        state = day_state(ds, imeis, manifest)
        if back < ALWAYS_REFETCH_DAYS:
            targets.append((ds, True))          # force: still accumulating
        elif state in ("pending", "partial"):
            targets.append((ds, False))
        # complete / empty_exhausted -> skip

    if not targets:
        print("[daemon] nothing to do; all days complete")
        return {"fetched_days": 0}

    print(f"[daemon] {len(targets)} day(s) to capture: "
          f"{', '.join(d for d, _ in targets[:8])}{' …' if len(targets) > 8 else ''}")

    total_rows = []
    for ds, force in targets:
        rows = capture_day(ds, imeis, api_key, api_url, workers, force)
        total_rows += rows
        with_data = sum(1 for r in rows if int(r["n_records"] or 0) > 0)

        entry = manifest.get(ds) or {}
        entry["last_attempt"] = datetime.now().isoformat(timespec="seconds")
        entry["devices_with_data"] = with_data
        entry["devices_fetched"] = len(rows)
        if with_data == 0:
            entry["status"] = "empty"
            entry["attempts"] = int(entry.get("attempts", 0)) + 1
            if entry["attempts"] >= MAX_EMPTY_ATTEMPTS:
                print(f"[daemon] {ds}: empty after {entry['attempts']} tries — "
                      f"treating as a permanent hole, will stop retrying")
        else:
            entry["status"] = "ok"
            entry["attempts"] = 0
        manifest[ds] = entry
        save_manifest(manifest)
        print(f"[daemon] {ds}: {with_data}/{len(rows)} devices reporting")

    if total_rows:
        append_coverage(total_rows)
    return {"fetched_days": len(targets), "rows": len(total_rows)}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--interval", type=float, default=900, help="Seconds between cycles")
    ap.add_argument("--lookback", type=int, default=45, help="Days back to keep healing")
    ap.add_argument("--workers", type=int, default=5)
    ap.add_argument("--once", action="store_true", help="Single pass, then exit")
    args = ap.parse_args()

    imeis = load_imeis()
    print(f"[daemon] fleet={len(imeis)} IMEIs  lookback={args.lookback}d  "
          f"interval={args.interval:.0f}s  store={OUT_DIR}")

    n = 0
    while True:
        n += 1
        started = time.time()
        print(f"\n[daemon] ===== cycle {n} @ {datetime.now():%Y-%m-%d %H:%M:%S} =====")
        try:
            cycle(imeis, args.lookback, args.workers)
        except Exception as e:
            print(f"[daemon] cycle failed: {e}")
        took = time.time() - started
        print(f"[daemon] cycle {n} done in {took:.0f}s")

        if args.once:
            return
        # Keep the cadence steady regardless of how long the cycle took.
        time.sleep(max(30.0, args.interval - took))


if __name__ == "__main__":
    main()
