#!/usr/bin/env python3
"""Evaluate detector v3 recall against verified fuel-theft cases, keyed by IMEI.

Identity resolution happens once in tools/build_ground_truth.py and lands in
fuel_data/ground_truth/theft_cases.csv. This script only consumes that file, so
there is a single source of truth for which case belongs to which vehicle.

History / why the rewrite
-------------------------
This harness used to match a spreadsheet vehicle name against the *truncated*
deviceName in the dump, using fuzzy scoring plus a 27-entry override table. Two
things were wrong with that:

  * The overrides aliased distinct trucks onto one device. Four separate
    vehicles resolved to "Ceigall LBG TM", three to "Ceigall LBG TIP", three to
    "RJ-14-GP-7755 Ananta Tipper". 23 of the 29 reported HITs came through an
    override, and 14 landed on the two Ceigall names alone -- i.e. a theft on
    truck A was being "caught" using truck B's fuel trace.
  * deviceName is not even unique: five June device names map to two IMEIs each.

The old docstring claimed the dump had "NO imei field". It does -- populated on
100% of records -- so the exact join was available all along.

Expect a LOWER hit count than the old 29. The old number was inflated.

Usage:
  cd fuel-theft-detector
  python tools/evaluate_known_thefts.py --data ../fuel_data/june_2026_data.json
"""

from __future__ import annotations

import argparse
import csv
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from data.fetch import normalize_records
from data.stream import load_by_imei_day
from data.vehicle_assignments import attach_vehicle_names
from detection.pipeline_v3 import run_session_pipeline

GROUND_TRUTH = ROOT.parent / "fuel_data" / "ground_truth" / "theft_cases.csv"

RANK = {"LIKELY_NOISE": 0, "MANUAL_REVIEW": 1, "AUTO_CONFIRMED": 2}

# Statuses that keep a case out of the recall denominator. These are data-coverage
# or identity problems, not detector failures, and folding them into recall would
# understate the detector exactly as aliasing previously overstated it.
NON_EVALUABLE = {"UNRESOLVED", "AMBIGUOUS", "NEEDS_REVIEW", "NO_DATA_ON_DAY"}


def _tol(expected: float, tol_frac: float, tol_abs: float) -> float:
    return max(tol_abs, expected * tol_frac)


def assign_matches(cases_on_day, drains, tol_frac=0.25, tol_abs=5.0):
    """Assign detected drains to ground-truth cases one-to-one, per vehicle-day.

    Two properties matter for an honest recall number:

      * One detection may satisfy only ONE case. Three cases of ~29 L on the same
        truck the same day are three separate thefts; if the detector produced a
        single 28.4 L drain it found one of them, not all three.
      * A single siphon is sometimes split by the session builder into consecutive
        drains. Summing adjacent drains recovers those (case #50: 117.33 + 87.58
        = 204.91 vs 204.37 expected), so they are matched and labelled 'split'.

    `drains` is a list of (magnitude, decision). Returns {case_index: match}.
    """
    unused = list(range(len(drains)))
    results = {}

    # Largest expected first: big thefts have the most distinctive magnitudes, so
    # matching them first avoids a small case stealing a large drain.
    order = sorted(range(len(cases_on_day)), key=lambda i: -cases_on_day[i])

    # Pass 1 -- single drain within tolerance.
    for ci in order:
        expected = cases_on_day[ci]
        tol = _tol(expected, tol_frac, tol_abs)
        best, best_err = None, None
        for di in unused:
            err = abs(drains[di][0] - expected)
            if err <= tol and (best_err is None or err < best_err):
                best, best_err = di, err
        if best is not None:
            unused.remove(best)
            results[ci] = {
                "magnitude": drains[best][0],
                "decision": drains[best][1],
                "kind": "single",
                "rel_err": best_err / expected if expected else 0.0,
            }

    # Pass 2 -- consecutive drains summing to the expected quantity (split session).
    #
    # Two guards keep this from manufacturing hits out of noise. Without them the
    # matcher would happily add eight ~12 L drains to "reach" a 112 L case, or six
    # small ones to reach 28 L on a truck where it had already found the real
    # 28.4 L drain. Both were observed before the guards were added.
    #   * at most MAX_SPLIT components -- a siphon split into 8 sessions is not a
    #     split, it is the noise floor
    #   * every component must be a meaningful share of the expected quantity
    # The group closest to the expected total wins, rather than the first one that
    # happens to land inside a tolerance that is very wide for large quantities.
    MAX_SPLIT = 3
    MIN_COMPONENT_FRAC = 0.15

    for ci in order:
        if ci in results:
            continue
        expected = cases_on_day[ci]
        tol = _tol(expected, tol_frac, tol_abs)
        floor = expected * MIN_COMPONENT_FRAC
        found = None
        best_err = None
        for start in range(len(unused)):
            total = 0.0
            group = []
            for di in unused[start:]:
                if drains[di][0] < floor:
                    break
                total += drains[di][0]
                group.append(di)
                if len(group) >= 2:
                    err = abs(total - expected)
                    if err <= tol and (best_err is None or err < best_err):
                        found, best_err = (list(group), total), err
                if len(group) >= MAX_SPLIT or total > expected + tol:
                    break
        if found:
            group, total = found
            for di in group:
                unused.remove(di)
            results[ci] = {
                "magnitude": round(total, 2),
                # Report the weakest tier in the group: the alert ops would act on
                # is only as strong as its least-confident component.
                "decision": min(
                    (drains[di][1] for di in group), key=lambda d: RANK.get(d, 0)
                ),
                "kind": f"split_x{len(group)}",
                "rel_err": abs(total - expected) / expected if expected else 0.0,
            }

    # Pass 3 -- a drain that covers the expected amount but is well outside
    # tolerance (e.g. 93 L detected vs 46.5 L reported). Something real was almost
    # certainly caught, but the quantities disagree too much to call it a clean
    # hit, so it is recorded separately and kept OUT of strict recall.
    for ci in order:
        if ci in results:
            continue
        expected = cases_on_day[ci]
        covering = [di for di in unused if drains[di][0] >= expected]
        if covering:
            di = min(covering, key=lambda d: drains[d][0])
            unused.remove(di)
            results[ci] = {
                "magnitude": drains[di][0],
                "decision": drains[di][1],
                "kind": "covering",
                "rel_err": abs(drains[di][0] - expected) / expected if expected else 0.0,
            }

    return results


def load_cases(path: Path) -> list:
    if not path.exists():
        raise SystemExit(
            f"Ground truth not found: {path}\nRun: python tools/build_ground_truth.py"
        )
    with path.open(encoding="utf-8") as f:
        return list(csv.DictReader(f))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="../fuel_data/june_2026_data.json")
    ap.add_argument("--ground-truth", default=str(GROUND_TRUTH))
    ap.add_argument("--out", default="output/reports/known_theft_evaluation.csv")
    ap.add_argument(
        "--min-decision",
        default="MANUAL_REVIEW",
        choices=["AUTO_CONFIRMED", "MANUAL_REVIEW", "LIKELY_NOISE"],
    )
    args = ap.parse_args()

    data_path = Path(args.data)
    if not data_path.exists():
        raise SystemExit(f"Data not found: {data_path}")

    cases = load_cases(Path(args.ground_truth))
    resolved = [c for c in cases if c.get("imei")]
    wanted_imeis = {c["imei"] for c in resolved}
    wanted_days = {c["event_date"] for c in resolved}

    print(f"Ground truth: {len(cases)} cases, {len(resolved)} resolved to an IMEI")
    print(f"Loading {data_path} for {len(wanted_imeis)} IMEIs ...")
    by_imei_day = load_by_imei_day(
        data_path,
        wanted_imeis,
        wanted_days,
        progress=lambda n: print(f"  scanned {n:,} records", flush=True),
    )
    print(f"Loaded {len(by_imei_day)} (imei, day) buckets")

    # Two cases on the same truck the same day are legitimate; flag so a reviewer
    # knows the two rows share one telemetry bucket and can double-count a hit.
    dupes = [k for k, n in Counter((c["imei"], c["event_date"]) for c in resolved).items() if n > 1]
    for imei, day in dupes:
        print(f"  NOTE: {len([c for c in resolved if (c['imei'], c['event_date']) == (imei, day)])} "
              f"cases share (imei={imei}, day={day})")

    min_rank = RANK[args.min_decision]

    def drains_for(imei: str, day: str):
        """Detected DRAIN events at/above --min-decision for one vehicle-day."""
        pts = by_imei_day.get((imei, day), [])
        if not pts:
            return None
        df = normalize_records(pts, imei=imei)
        df = attach_vehicle_names(df)
        _, ev = run_session_pipeline(df)
        if ev is None or ev.empty:
            return []
        d = ev[ev["event_type"] == "DRAIN"].copy()
        d = d[d["decision"].map(RANK).fillna(0) >= min_rank]
        d = d.sort_values("start_time") if "start_time" in d.columns else d
        return list(zip(d["magnitude"].astype(float).tolist(), d["decision"].tolist()))

    # Group cases by vehicle-day so detections can be assigned one-to-one.
    groups: dict = {}
    for idx, c in enumerate(cases):
        if c.get("imei"):
            groups.setdefault((c["imei"], c["event_date"]), []).append(idx)

    results: dict = {}
    points_by_case: dict = {}
    for (imei, day), idxs in groups.items():
        drains = drains_for(imei, day)
        points_by_case.update({i: len(by_imei_day.get((imei, day), [])) for i in idxs})
        if drains is None:
            continue
        expected = [float(cases[i]["liters"] or 0) for i in idxs]
        assigned = assign_matches(expected, drains)
        for local, i in enumerate(idxs):
            if local in assigned:
                results[i] = assigned[local]
            else:
                results[i] = {"unmatched_drains": sorted((m for m, _ in drains), reverse=True)[:5]}

    rows_out = []
    for idx, c in enumerate(cases):
        status = c["resolution_status"]
        imei, day = c.get("imei", ""), c["event_date"]
        qty = float(c["liters"] or 0)
        detected = decision = None
        kind = ""
        rel_err = ""
        points = points_by_case.get(idx, 0)
        note = c.get("notes", "")

        if imei:
            if points == 0:
                status = "NO_DATA_ON_DAY"
                note = "imei resolved but no telemetry that date"
            else:
                r = results.get(idx, {})
                if "magnitude" in r:
                    detected = r["magnitude"]
                    decision = r["decision"]
                    kind = r["kind"]
                    rel_err = round(r["rel_err"], 3)
                    status = "HIT_COVERING" if kind == "covering" else "HIT"
                    note = "" if kind == "single" else f"match={kind}"
                else:
                    status = "MISS"
                    note = f"unmatched_drains={r.get('unmatched_drains', [])}"

        rows_out.append(
            {
                "case_id": c["case_id"],
                "case_vehicle": c["vehicle_name"],
                "imei": imei,
                "date": day,
                "expected_qty_l": qty,
                "match_method": c["match_method"],
                "points_on_day": points,
                "status": status,
                "detected_qty_l": detected if detected is not None else "",
                "detected_decision": decision or "",
                "match_kind": kind,
                "rel_err": rel_err,
                "note": note,
            }
        )
        print(f"[{status}] #{c['case_id']} {c['vehicle_name'][:40]} {day} {qty}L det={detected}")

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows_out[0].keys()))
        w.writeheader()
        w.writerows(rows_out)

    counts = Counter(r["status"] for r in rows_out)
    hits = counts["HIT"]
    covering = counts["HIT_COVERING"]
    misses = counts["MISS"]
    evaluable = hits + covering + misses

    print("\n==== SUMMARY ====")
    for k, v in counts.most_common():
        marker = "" if k in ("HIT", "HIT_COVERING", "MISS") else "   (excluded from recall)"
        print(f"  {k:16s} {v:3d}{marker}")
    print()
    if evaluable:
        print(f"Strict recall  : {hits}/{evaluable} = {hits/evaluable:.1%}   "
              f"(quantity matches within tolerance)")
        print(f"Lenient recall : {hits + covering}/{evaluable} = "
              f"{(hits + covering)/evaluable:.1%}   "
              f"(also counts {covering} 'covering' detections whose quantity disagrees)")
    else:
        print("Recall: no evaluable cases")
    print(f"Coverage       : {evaluable}/{len(rows_out)} cases evaluable "
          f"({evaluable/len(rows_out):.0%})")

    if hits or covering:
        by_dec = Counter(
            r["detected_decision"] for r in rows_out if r["status"].startswith("HIT")
        )
        by_kind = Counter(r["match_kind"] for r in rows_out if r["match_kind"])
        print(f"Hits by tier   : {dict(by_dec)}")
        print(f"Match kinds    : {dict(by_kind)}")
    methods = Counter(
        r["match_method"] for r in rows_out if r["status"] in ("HIT", "HIT_COVERING", "MISS")
    )
    print(f"Match methods  : {dict(methods)}")

    print(
        "\nExcluded-status meanings:\n"
        "  UNRESOLVED    - vehicle has no IMEI in device_imei_map (likely not instrumented)\n"
        "  NEEDS_REVIEW  - near-miss plate; confirm in theft_cases.csv, then set imei\n"
        "  AMBIGUOUS     - plate maps to >1 IMEI; needs a time-aware assignment\n"
        "  NO_DATA_ON_DAY- IMEI resolved but the dump has no points that date\n"
        f"\nWrote {out_path}"
    )


if __name__ == "__main__":
    main()
