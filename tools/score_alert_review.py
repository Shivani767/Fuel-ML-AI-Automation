#!/usr/bin/env python3
"""Score a filled alert review sheet (precision / by type).

Usage:
  .venv/bin/python tools/score_alert_review.py --day 2026-08-19
"""

from __future__ import annotations

import argparse
import csv
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REVIEWS = ROOT / "fuel_data" / "alerts" / "reviews"


def score(day: str) -> None:
    path = REVIEWS / f"{day}_review.csv"
    if not path.exists():
        raise SystemExit(f"Missing {path} — run build_alert_review_sheet.py first.")
    rows = list(csv.DictReader(path.open(encoding="utf-8")))
    if not rows:
        raise SystemExit("Review sheet is empty.")

    norm = []
    for r in rows:
        v = str(r.get("verdict") or "").strip().upper()
        if v in ("CORRECT", "OK", "YES", "TRUE", "1", "✅"):
            v = "CORRECT"
        elif v in ("WRONG", "FALSE", "NO", "0", "❌"):
            v = "WRONG"
        elif v in ("UNSURE", "?", "UNKNOWN"):
            v = "UNSURE"
        else:
            v = "PENDING"
        norm.append((v, r))

    counts = Counter(v for v, _ in norm)
    judged = [r for v, r in norm if v in ("CORRECT", "WRONG")]
    correct = sum(1 for v, _ in norm if v == "CORRECT")
    wrong = sum(1 for v, _ in norm if v == "WRONG")
    precision = correct / (correct + wrong) if (correct + wrong) else None

    print(f"Day {day}: {len(rows)} alerts")
    print(f"  verdicts: {dict(counts)}")
    if precision is None:
        print("  precision: n/a (mark CORRECT/WRONG first)")
    else:
        print(f"  precision: {precision:.1%}  ({correct} correct / {correct + wrong} judged)")

    by_type = Counter()
    by_type_ok = Counter()
    for v, r in norm:
        if v not in ("CORRECT", "WRONG"):
            continue
        t = r.get("eventType") or "?"
        by_type[t] += 1
        if v == "CORRECT":
            by_type_ok[t] += 1
    for t, n in by_type.items():
        print(f"  {t}: {by_type_ok[t]}/{n} = {by_type_ok[t]/n:.0%}")

    pending = [r for v, r in norm if v == "PENDING"]
    if pending:
        print(f"\nStill unmarked ({len(pending)}):")
        for r in pending[:10]:
            print(f"  - {r.get('review_id')} {r.get('eventType')} {r.get('deviceName')} {r.get('volume_L')}L @ {r.get('eventTime')}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--day", required=True)
    args = ap.parse_args()
    score(args.day)


if __name__ == "__main__":
    main()
