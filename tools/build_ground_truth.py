#!/usr/bin/env python3
"""Build the single source of truth for verified theft cases, keyed by IMEI.

Why this exists
---------------
The evaluation harness used to match a spreadsheet vehicle name against the
*truncated* deviceName in the telemetry dump, via fuzzy scoring plus a hand-written
override table. That aliased distinct trucks onto one device's telemetry (four
separate vehicles all resolved to "Ceigall LBG TM"), so a theft on truck A was
scored against truck B's fuel trace.

The telemetry actually carries a populated `imei` on every record, and
fuel_data/device_imei_map.json maps vehicle names to clean 15-digit IMEIs.
So identity resolution should happen once, here, on ~80 rows -- not per-lookup
against millions of telemetry rows.

Resolution is deliberately conservative. A case is only auto-resolved when the
vehicle *plate* matches, or the full name matches exactly. Anything softer is
emitted as NEEDS_REVIEW with its candidate, for a human to confirm. Nothing is
matched on brand/family resemblance, because that is what produced the aliasing.

Output: fuel_data/ground_truth/theft_cases.csv
"""

from __future__ import annotations

import argparse
import csv
import json
import re
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CASES_PATH = ROOT / "fuel_data" / "labeled_theft_cases.json"
IMEI_MAP_PATH = ROOT / "fuel_data" / "device_imei_map.json"
OUT_PATH = ROOT / "fuel_data" / "ground_truth" / "theft_cases.csv"

# Indian registration plate, tolerating '-', ' ' or nothing as separators.
# Deliberately requires the trailing 3-4 digit serial so equipment codes like
# "DG-100Kva" or "SR-YQ12178" are not mistaken for plates.
PLATE_RE = re.compile(
    r"\b([A-Z]{2})[-\s]?(\d{1,2})[-\s]?([A-Z]{0,3})[-\s]?(\d{3,4})\b",
    re.IGNORECASE,
)

FIELDNAMES = [
    "case_id",
    "imei",
    "vehicle_name",
    "matched_map_name",
    "event_date",
    "event_time",
    "liters",
    "source",
    "resolution_status",
    "match_method",
    "notes",
]


def norm_name(s: str) -> str:
    """Lowercase, collapse non-alphanumerics. For exact-name comparison only."""
    return re.sub(r"[^a-z0-9]+", "", str(s or "").lower())


def plates(s: str) -> set:
    """Extract normalised plate keys, e.g. 'PB-10-HH-7781' -> 'pb10hh7781'."""
    out = set()
    for m in PLATE_RE.finditer(str(s or "")):
        state, dist, series, serial = m.groups()
        out.add(f"{state}{dist}{series}{serial}".lower())
    return out


def plate_parts(p: str):
    """Split a normalised plate key back into (alpha_prefix, numeric_serial)."""
    m = re.match(r"^([a-z]+\d{1,2}[a-z]*?)(\d{3,4})$", p)
    return m.groups() if m else (p, "")


def one_edit_apart(a: str, b: str) -> bool:
    """True if two equal-length serials differ by one substitution or one swap."""
    if len(a) != len(b) or a == b:
        return False
    diff = [i for i, (x, y) in enumerate(zip(a, b)) if x != y]
    if len(diff) == 1:
        return True
    if len(diff) == 2:
        i, j = diff
        # adjacent transposition, e.g. 7081 <-> 7801
        if j == i + 1 and a[i] == b[j] and a[j] == b[i]:
            return True
        # same two digits swapped at any distance, e.g. 7081 <-> 7781 style typos
        if sorted(a[i] + a[j]) == sorted(b[i] + b[j]):
            return True
    return False


def build_indexes(imei_map: dict):
    """Index the name->IMEI map by exact normalised name and by plate."""
    by_name = {}
    by_plate = defaultdict(set)
    for name, imei in imei_map.items():
        imei = str(imei).strip()
        by_name[norm_name(name)] = (name, imei)
        for p in plates(name):
            by_plate[p].add((name, imei))
    return by_name, by_plate


def resolve(case_name: str, by_name: dict, by_plate: dict):
    """Return (imei, matched_name, status, method, note).

    Order matters: plate is the strongest identity signal, so it is tried before
    the full-string match (names carry inconsistent suffixes like "(New)"/"TM").
    """
    case_plates = plates(case_name)

    # 1. Exact plate match -- unambiguous vehicle identity.
    hits = {c for p in case_plates for c in by_plate.get(p, ())}
    if len({imei for _, imei in hits}) == 1:
        name, imei = sorted(hits)[0]
        return imei, name, "RESOLVED", "plate", ""
    if len({imei for _, imei in hits}) > 1:
        return "", "", "AMBIGUOUS", "plate", (
            "plate matches multiple IMEIs: " + ",".join(sorted({i for _, i in hits}))
        )

    # 2. Exact normalised full-name match.
    hit = by_name.get(norm_name(case_name))
    if hit:
        return hit[1], hit[0], "RESOLVED", "exact_name", ""

    # 3. Near-miss plate (single-digit typo / transposition) -- surfaced, never
    #    auto-accepted. This is how "PB-10-HH-7081" vs "PB-10-HH-7781" gets seen.
    for cp in case_plates:
        c_alpha, c_serial = plate_parts(cp)
        for mp, entries in by_plate.items():
            m_alpha, m_serial = plate_parts(mp)
            if c_alpha == m_alpha and one_edit_apart(c_serial, m_serial):
                names = sorted({n for n, _ in entries})
                imeis = sorted({i for _, i in entries})
                if len(imeis) == 1:
                    return "", names[0], "NEEDS_REVIEW", "plate_near_miss", (
                        f"case plate {cp} vs map plate {mp} "
                        f"(candidate imei {imeis[0]}) - confirm same vehicle"
                    )

    # 4. No responsible match. Explicitly NOT falling back to brand/family
    #    similarity -- that is what aliased four trucks onto one device.
    return "", "", "UNRESOLVED", "none", "no plate or exact-name match in device_imei_map"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--cases", default=str(CASES_PATH))
    ap.add_argument("--imei-map", default=str(IMEI_MAP_PATH))
    ap.add_argument("--out", default=str(OUT_PATH))
    args = ap.parse_args()

    cases = json.loads(Path(args.cases).read_text())
    imei_map = json.loads(Path(args.imei_map).read_text())
    by_name, by_plate = build_indexes(imei_map)

    print(f"cases={len(cases)}  map_names={len(imei_map)}  map_imeis={len(set(imei_map.values()))}")

    rows = []
    for c in cases:
        name = str(c.get("vehicle_name") or "").strip()
        imei, matched, status, method, note = resolve(name, by_name, by_plate)
        rows.append(
            {
                "case_id": c.get("sno"),
                "imei": imei,
                "vehicle_name": name,
                "matched_map_name": matched,
                "event_date": str(c.get("date") or "")[:10],
                # Source spreadsheet is date-only. Left blank rather than faking
                # T00:00:00, which previously forced an 18-hour match window.
                "event_time": "",
                "liters": c.get("qty"),
                "source": "user_spreadsheet_2026_06",
                "resolution_status": status,
                "match_method": method,
                "notes": note,
            }
        )

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDNAMES)
        w.writeheader()
        w.writerows(rows)

    # ---- Report -------------------------------------------------------------
    status_counts = Counter(r["resolution_status"] for r in rows)
    method_counts = Counter(r["match_method"] for r in rows if r["imei"])
    print("\n==== RESOLUTION ====")
    for k, v in status_counts.most_common():
        print(f"  {k:14s} {v:3d} cases")
    print(f"  (auto-resolved by: {dict(method_counts)})")

    resolved_imeis = {r["imei"] for r in rows if r["imei"]}
    print(f"  distinct IMEIs resolved: {len(resolved_imeis)}")

    review = [r for r in rows if r["resolution_status"] == "NEEDS_REVIEW"]
    if review:
        print(f"\n==== NEEDS_REVIEW ({len(review)}) -- confirm each, then set imei ====")
        for r in review:
            print(f"  #{r['case_id']:>3} {r['vehicle_name']}\n        {r['notes']}")

    unresolved = sorted({r["vehicle_name"] for r in rows if r["resolution_status"] == "UNRESOLVED"})
    if unresolved:
        print(f"\n==== UNRESOLVED ({len(unresolved)} distinct vehicles) ====")
        print("  Mark each as 'resolved to IMEI x' or 'not instrumented'.")
        for n in unresolved:
            print(f"  - {n}")

    print(f"\nWrote {out}")


if __name__ == "__main__":
    main()
