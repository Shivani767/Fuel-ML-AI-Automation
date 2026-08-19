#!/usr/bin/env python3
"""Build a human review sheet for one day's alert agent CSV.

Usage:
  .venv/bin/python tools/build_alert_review_sheet.py --day 2026-08-19
  # then open fuel_data/alerts/reviews/<day>_review.csv
  # fill verdict: CORRECT | WRONG | UNSURE
  .venv/bin/python tools/score_alert_review.py --day 2026-08-19
"""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.services.alerts import precision_gate_preview  # noqa: E402

DAILY = ROOT / "fuel_data" / "alerts" / "daily"
REVIEWS = ROOT / "fuel_data" / "alerts" / "reviews"

FIELDS = [
    "review_id",
    "verdict",  # CORRECT | WRONG | UNSURE — fill by hand after FuelArk check
    "notes",
    "would_new_gates_suppress",  # empty = keep; else reason new precision gates would drop it
    "eventType",
    "deviceName",
    "imei",
    "eventTime",
    "endTime",
    "volume_L",
    "confidence",
    "decision",
    "ignition",
    "trulyParked",
    "tamper",
    "durationMinutes",
    "speed",
    "mlLabel",
    "mlConfidence",
    "latitude",
    "longitude",
    "confirmedAt",
    "fuelark_check",
]


def build(day: str) -> Path:
    src = DAILY / f"{day}_alerts.csv"
    if not src.exists():
        raise SystemExit(f"Missing {src} — run the 30-min pipeline first.")
    rows_in = list(csv.DictReader(src.open(encoding="utf-8")))
    REVIEWS.mkdir(parents=True, exist_ok=True)
    out = REVIEWS / f"{day}_review.csv"
    # Preserve prior verdicts if re-building
    prior = {}
    if out.exists():
        for r in csv.DictReader(out.open(encoding="utf-8")):
            key = (r.get("deviceName"), r.get("eventType"), r.get("eventTime"), r.get("volume_L"))
            prior[key] = (r.get("verdict") or "", r.get("notes") or "")

    out_rows = []
    for i, r in enumerate(rows_in, 1):
        key = (r.get("deviceName"), r.get("eventType"), r.get("eventTime"), r.get("volume_L"))
        verdict, notes = prior.get(key, ("", ""))
        hint = precision_gate_preview(r)
        out_rows.append(
            {
                "review_id": f"{day}-{i:03d}",
                "verdict": verdict,
                "notes": notes,
                "would_new_gates_suppress": hint,
                "eventType": r.get("eventType"),
                "deviceName": r.get("deviceName"),
                "imei": r.get("imei"),
                "eventTime": r.get("eventTime"),
                "endTime": r.get("endTime"),
                "volume_L": r.get("volume_L"),
                "confidence": r.get("confidence"),
                "decision": r.get("decision"),
                "ignition": r.get("ignition"),
                "trulyParked": r.get("trulyParked"),
                "tamper": r.get("tamper"),
                "durationMinutes": r.get("durationMinutes"),
                "speed": r.get("speed", ""),
                "mlLabel": r.get("mlLabel", ""),
                "mlConfidence": r.get("mlConfidence", ""),
                "latitude": r.get("latitude"),
                "longitude": r.get("longitude"),
                "confirmedAt": r.get("confirmedAt"),
                "fuelark_check": (
                    f"Open FuelArk for IMEI {r.get('imei')} around {r.get('eventTime')} "
                    f"and confirm {r.get('eventType')} ~{r.get('volume_L')} L"
                ),
            }
        )
    with out.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(out_rows)
    would_drop = sum(1 for r in out_rows if r["would_new_gates_suppress"])
    print(f"Wrote {len(out_rows)} rows -> {out}")
    print(f"New precision gates would suppress {would_drop}/{len(out_rows)} of these released alerts")
    print("Fill column 'verdict' with CORRECT / WRONG / UNSURE, then run score_alert_review.py")
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--day", required=True, help="YYYY-MM-DD")
    args = ap.parse_args()
    build(args.day)


if __name__ == "__main__":
    main()
