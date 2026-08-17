#!/usr/bin/env python3
"""Generate fuel-theft-detector/data/vehicle_assignments.csv from the IMEI map.

The assignments file shipped with a single placeholder row, so every session
resolved to vehicle_type "default". Two things followed from that:

  * _session_expected_burn() always used the default burn rates, so the
    ignition-ON residual (raw drop minus expected consumption) was wrong for any
    vehicle that is not an average truck.
  * vehicle_type_code came out of training with feature importance 0.000000.

device_imei_map.json holds 153 vehicle-name aliases over 99 clean IMEIs, and
covers all 94 IMEIs present in the June dump. This collapses it to one row per
IMEI, picking the most descriptive alias, and infers vehicle_type from the name.

Burn rates are only defined in config.DEFAULT_BURN_RATES for truck / tractor /
tipper / car / dg. Construction equipment (excavators, rollers, pavers, cranes)
has no entry, and guessing one would corrupt the residual calculation, so those
are left as "default" and reported for a human to supply real rates.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
IMEI_MAP = ROOT / "fuel_data" / "device_imei_map.json"
OUT = ROOT / "fuel-theft-detector" / "data" / "vehicle_assignments.csv"

# Ordered most-specific first: "Ananta Tipper" must classify as tipper, not truck.
# Note "kva" carries no left \b -- ratings appear glued to digits ("82..5Kva").
TYPE_RULES = [
    ("tipper", r"\b(tipper|tip|hyva)\b"),
    ("dg", r"\b(dg|genset|gen\s*set)\b|kva\b"),
    ("tractor", r"\btractor\b"),
    ("car", r"\b(car|camper|pickup|bolero|jeep)\b"),
    # "trailor" is a recurring misspelling in the roster.
    ("truck", r"\b(tm|transit\s*mixer|trailers?|trailor|truck|hydra|lifter|lift|tanker)\b"),
]

# Recognised but unpriced: no burn rates exist for these in config.
# "ex-\d+" must consume the whole number, else the trailing \b fails on "ex-15".
EQUIPMENT = r"\b(excavator|ex-\d+|roller|paver|compactor|crane|carne|jcb|crusher|" \
            r"sany|kobelco|dynapac|batching|soil|loader|dozer|drill|boom|rig|" \
            r"tower\s*wagon)\b"

FIELDNAMES = ["imei", "vehicle_name", "valid_from", "valid_to", "vehicle_type"]

# The dump starts 2026-05-03; open-ended on the right so current data resolves.
VALID_FROM = "2026-01-01"


def classify(name: str) -> tuple:
    """Return (vehicle_type, is_unpriced_equipment)."""
    n = re.sub(r"[^a-z0-9\s-]", " ", name.lower())
    n = re.sub(r"\s+", " ", n)
    if re.search(EQUIPMENT, n):
        return "default", True
    for vtype, pattern in TYPE_RULES:
        if re.search(pattern, n):
            return vtype, False
    return "default", False


def best_alias(names: list) -> str:
    """Prefer the alias carrying a registration plate, then the longest."""
    plated = [n for n in names if re.search(r"[A-Z]{2}[-\s]?\d{1,2}[-\s]?[A-Z]{0,3}[-\s]?\d{3,4}", n)]
    pool = plated or names
    return max(pool, key=len)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--imei-map", default=str(IMEI_MAP))
    ap.add_argument("--out", default=str(OUT))
    ap.add_argument("--valid-from", default=VALID_FROM)
    args = ap.parse_args()

    imei_map = json.loads(Path(args.imei_map).read_text())

    by_imei = defaultdict(list)
    for name, imei in imei_map.items():
        by_imei[str(imei).strip()].append(name)

    rows = []
    unpriced = []
    for imei in sorted(by_imei):
        name = best_alias(by_imei[imei])
        vtype, is_equip = classify(name)
        if is_equip:
            unpriced.append((imei, name))
        rows.append(
            {
                "imei": imei,
                "vehicle_name": name,
                "valid_from": args.valid_from,
                "valid_to": "",
                "vehicle_type": vtype,
            }
        )

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDNAMES)
        w.writeheader()
        w.writerows(rows)

    print(f"{len(imei_map)} aliases -> {len(rows)} IMEIs")
    print(f"types: {dict(Counter(r['vehicle_type'] for r in rows).most_common())}")
    print(f"\nWrote {out}")

    if unpriced:
        print(
            f"\n==== {len(unpriced)} units classified as construction equipment ====\n"
            "Left as vehicle_type='default'. config.DEFAULT_BURN_RATES has no entry for\n"
            "these, and an excavator idles far above the 0.8 L/h default, so the\n"
            "ignition-ON residual will understate drains until real rates are added."
        )
        for imei, name in unpriced:
            print(f"  {imei}  {name}")


if __name__ == "__main__":
    main()
