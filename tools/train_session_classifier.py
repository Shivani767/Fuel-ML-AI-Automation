#!/usr/bin/env python3
"""Train multi-class session classifier: theft / fill / sensor_fault / noise.

Primary: RandomForest (baseline) → optional XGBoost if installed.
Secondary: IsolationForest on daily consumption residual (slow/persistent theft).

One training row = one consolidated session (never raw ticks).
Labels:
  - theft: matches verified_theft_events.json (spreadsheet) OR AUTO_CONFIRMED DRAIN
  - fill: AUTO_CONFIRMED FILL (and verified fills if present)
  - sensor_fault: oscillation / channel_disagreement / bounce glitch
  - noise: LIKELY_NOISE / remaining MANUAL_REVIEW drains&fills under review band
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import IsolationForest, RandomForestClassifier
from sklearn.metrics import classification_report, confusion_matrix
from sklearn.model_selection import train_test_split
from sklearn.preprocessing import LabelEncoder

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "fuel-theft-detector"))

from backend.services.vehicle_aliases import (  # noqa: E402
    names_for_device,
    spreadsheet_to_device,
    to_display_name,
)
from data.fetch import normalize_records  # noqa: E402
from detection.pipeline_v3 import run_session_pipeline  # noqa: E402

FEATURE_COLS = [
    "magnitude",
    "raw_magnitude",
    "residual_magnitude",
    "expected_burn_liters",
    "duration_minutes",
    "num_ticks",
    "ignition_off",
    "truly_parked",
    "bounced_back",
    "plateau_stable",
    "channel_disagreement",
    "tamper_flag",
    "oscillation",
    "low_internal_voltage",
    "speed_kmh",
    "hour_of_day",
    "day_of_week",
    "is_drain",
    "vehicle_type_code",
]

LABELS = ["theft", "fill", "sensor_fault", "noise"]


def _load_json(path: Path) -> list:
    if not path.exists():
        return []
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _parse_ts(value) -> Optional[pd.Timestamp]:
    if value is None or value == "":
        return None
    try:
        ts = pd.Timestamp(value)
        # Compare calendar days in naive local wall-clock
        if getattr(ts, "tzinfo", None) is not None or getattr(ts, "tz", None) is not None:
            try:
                ts = ts.tz_convert(None)
            except (TypeError, AttributeError):
                ts = ts.tz_localize(None)
        return ts
    except Exception:
        return None


def _day_key(ts: pd.Timestamp) -> pd.Timestamp:
    return pd.Timestamp(ts.date())


def load_verified_thefts(path: Path) -> List[dict]:
    rows = _load_json(path)
    out = []
    for e in rows:
        if not e.get("verified", True):
            continue
        et = str(e.get("eventType") or "").lower()
        if "theft" not in et and "fill" not in et:
            # default spreadsheet rows are thefts
            et = "theft"
        name = str(e.get("deviceName") or "").strip()
        ts = _parse_ts(e.get("eventTime"))
        qty = float(e.get("fuelDecrease") or e.get("fuelIncrease") or 0)
        out.append(
            {
                "name": name,
                "names": {n.lower() for n in names_for_device(name)}
                | {name.lower(), spreadsheet_to_device(name).lower()},
                "day": _day_key(ts) if ts is not None else None,
                "qty": qty,
                "kind": "fill" if "fill" in et else "theft",
            }
        )
    return out


def match_verified(session: dict, verified: List[dict], window_hours: float = 18.0) -> Optional[str]:
    """Return label if session matches a verified spreadsheet case."""
    s_name = str(session.get("vehicle") or session.get("vehicle_name") or "").strip()
    s_names = {n.lower() for n in names_for_device(s_name)} | {
        s_name.lower(),
        spreadsheet_to_device(s_name).lower(),
        to_display_name(s_name).lower(),
    }
    st = _parse_ts(session.get("start_time") or session.get("timestamp"))
    if st is None:
        return None
    mag = float(session.get("raw_magnitude") or session.get("magnitude") or 0)
    stype = str(session.get("type") or "").upper()

    best = None
    best_score = 1e18
    for v in verified:
        if not (v["names"] & s_names):
            continue
        if v["day"] is None:
            continue
        # same calendar day (IST-ish wall clock already in dump)
        s_day = _day_key(st)
        if s_day != v["day"] and abs((s_day - v["day"]).total_seconds()) > 86400:
            continue
        # direction
        if v["kind"] == "theft" and stype != "DRAIN":
            continue
        if v["kind"] == "fill" and stype != "FILL":
            continue
        # magnitude proximity (prefer closest)
        qty = v["qty"]
        if qty > 0 and mag > 0:
            rel = abs(mag - qty) / max(qty, 1.0)
            if rel > 0.55 and abs(mag - qty) > 8:
                continue
            score = abs(mag - qty)
        else:
            score = 50.0
        # time distance
        score += abs((s_day - v["day"]).total_seconds()) / 3600.0
        if score < best_score:
            best_score = score
            best = v["kind"]
    return best


def label_session(session: dict, verified: List[dict]) -> str:
    verified_hit = match_verified(session, verified)
    if verified_hit:
        return verified_hit

    decision = str(session.get("decision") or "")
    stype = str(session.get("type") or "").upper()
    mag = float(session.get("magnitude") or 0)

    if session.get("oscillation") or session.get("discarded_as_glitch"):
        return "sensor_fault"
    if session.get("channel_disagreement") and mag < 8 and decision == "LIKELY_NOISE":
        return "sensor_fault"

    if decision == "AUTO_CONFIRMED":
        return "fill" if stype == "FILL" else "theft"
    if decision == "LIKELY_NOISE":
        return "noise"
    # MANUAL_REVIEW: soft negatives unless strong drain parked+tamper
    if session.get("tamper_flag") and stype == "DRAIN" and session.get("truly_parked"):
        return "theft"
    if stype == "FILL" and mag >= 10:
        return "fill"
    return "noise"


def session_feature_row(session: dict, vehicle_type_code: int) -> dict:
    ign = str(session.get("ignition_status") or session.get("ignition") or "").lower()
    st = _parse_ts(session.get("start_time") or session.get("timestamp"))
    return {
        "magnitude": float(session.get("magnitude") or 0),
        "raw_magnitude": float(session.get("raw_magnitude") or session.get("magnitude") or 0),
        "residual_magnitude": float(
            session.get("residual_magnitude") or session.get("magnitude") or 0
        ),
        "expected_burn_liters": float(session.get("expected_burn_liters") or 0),
        "duration_minutes": float(session.get("duration_minutes") or 0),
        "num_ticks": int(session.get("num_ticks") or 0),
        "ignition_off": 1 if ign in ("off", "0", "false") else 0,
        "truly_parked": 1 if session.get("truly_parked") else 0,
        "bounced_back": 1 if session.get("bounced_back") else 0,
        "plateau_stable": 1 if session.get("plateau_stable") else 0,
        "channel_disagreement": 1 if session.get("channel_disagreement") else 0,
        "tamper_flag": 1 if session.get("tamper_flag") else 0,
        "oscillation": 1 if session.get("oscillation") else 0,
        "low_internal_voltage": 1 if session.get("low_internal_voltage") else 0,
        "speed_kmh": float(session.get("speed_kmh") or 0),
        "hour_of_day": int(st.hour) if st is not None else 0,
        "day_of_week": int(st.dayofweek) if st is not None else 0,
        "is_drain": 1 if str(session.get("type") or "").upper() == "DRAIN" else 0,
        "vehicle_type_code": vehicle_type_code,
        "imei": str(session.get("imei") or ""),
        "vehicle": str(session.get("vehicle") or session.get("vehicle_name") or ""),
        "start_time": str(session.get("start_time") or ""),
        "type": str(session.get("type") or ""),
        "decision": str(session.get("decision") or ""),
    }


def _sessions_for_imei(args: Tuple[str, Dict[str, list], int]) -> Tuple[str, int, int, Optional[pd.DataFrame]]:
    """Worker: build sessions for one IMEI across its June days."""
    imei, by_day, stride = args
    day_sessions = []
    total_pts = 0
    for day in sorted(by_day):
        rows = by_day[day]
        if stride > 1:
            rows = rows[::stride]
        total_pts += len(rows)
        df = normalize_records(rows, imei=imei)
        if df.empty or len(df) < 3:
            continue
        _, sessions = run_session_pipeline(df)
        if sessions is not None and not sessions.empty:
            # Drop ultra-noisy tiny sessions early (keep >=2L residual already in pipeline)
            day_sessions.append(sessions)
    if not day_sessions:
        return imei, total_pts, 0, None
    out = pd.concat(day_sessions, ignore_index=True)
    # Avoid FutureWarning from all-NA object cols
    return imei, total_pts, len(out), out


def build_sessions_from_points(
    points: List[dict], workers: int = 6, stride: int = 2
) -> pd.DataFrame:
    """Run v3 session pipeline day-by-day per IMEI (parallel across IMEIs)."""
    by_imei_day: Dict[str, Dict[str, list]] = defaultdict(lambda: defaultdict(list))
    for p in points:
        imei = str(p.get("imei") or "").strip()
        t = str(p.get("deviceDataInsertTime") or "")
        day = t[:10]
        if not imei or len(day) < 10:
            continue
        by_imei_day[imei][day].append(p)

    imeis = sorted(by_imei_day)
    tasks = [(imei, dict(by_imei_day[imei]), max(1, int(stride))) for imei in imeis]
    all_sessions = []
    workers = max(1, int(workers))

    from concurrent.futures import ThreadPoolExecutor, as_completed

    print(f"  parallel workers={workers} imeis={len(tasks)} stride={stride}", flush=True)
    done = 0
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(_sessions_for_imei, t): t[0] for t in tasks}
        for fut in as_completed(futs):
            imei, total_pts, n, frame = fut.result()
            done += 1
            print(
                f"  [{done}/{len(tasks)}] {imei}: {total_pts} pts -> {n} sessions",
                flush=True,
            )
            if frame is not None and not frame.empty:
                all_sessions.append(frame)

    if not all_sessions:
        return pd.DataFrame()
    return pd.concat(all_sessions, ignore_index=True)


def build_training_frame(
    data_path: Path,
    verified_path: Path,
    max_points: Optional[int] = None,
    workers: int = 8,
    stride: int = 2,
    sessions_cache: Optional[Path] = None,
) -> pd.DataFrame:
    print(f"Loading {data_path} ...", flush=True)
    points = _load_json(data_path)
    if max_points:
        points = points[:max_points]
    print(f"  points={len(points)}", flush=True)

    verified = load_verified_thefts(verified_path)
    print(f"  verified labels={len(verified)}", flush=True)

    sessions = pd.DataFrame()
    if sessions_cache and sessions_cache.exists():
        print(f"Loading cached sessions from {sessions_cache}", flush=True)
        sessions = pd.read_pickle(sessions_cache)
    else:
        print("Building sessions (v3 pipeline)...", flush=True)
        sessions = build_sessions_from_points(points, workers=workers, stride=stride)
        if sessions_cache and not sessions.empty:
            sessions_cache.parent.mkdir(parents=True, exist_ok=True)
            sessions.to_pickle(sessions_cache)
            print(f"Cached sessions → {sessions_cache} ({len(sessions)} rows)", flush=True)
    if sessions.empty:
        raise RuntimeError("No sessions built — check input data")

    # vehicle type encoding
    vtypes = sessions.get("vehicle_type", pd.Series(["default"] * len(sessions))).fillna("default")
    type_codes = {t: i for i, t in enumerate(sorted(set(vtypes.astype(str))))}

    rows = []
    for _, s in sessions.iterrows():
        d = s.to_dict()
        vt = str(d.get("vehicle_type") or "default")
        feat = session_feature_row(d, type_codes.get(vt, 0))
        feat["label"] = label_session(d, verified)
        rows.append(feat)

    frame = pd.DataFrame(rows)
    print("Label distribution:")
    print(frame["label"].value_counts().to_string())
    return frame


def train_random_forest(X: pd.DataFrame, y: np.ndarray, seed: int = 42):
    # Stratify only if every class has >=2 samples
    counts = pd.Series(y).value_counts()
    strat = y if counts.min() >= 2 and len(counts) > 1 else None
    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=0.25, random_state=seed, stratify=strat
    )
    model = RandomForestClassifier(
        n_estimators=300,
        max_depth=16,
        min_samples_split=4,
        min_samples_leaf=2,
        class_weight="balanced",
        random_state=seed,
        n_jobs=-1,
    )
    model.fit(X_train, y_train)
    pred = model.predict(X_test)
    return model, X_train, X_test, y_train, y_test, pred


def train_xgboost(X: pd.DataFrame, y: np.ndarray, seed: int = 42):
    try:
        from xgboost import XGBClassifier
    except Exception as e:
        print(f"xgboost unavailable ({e}) — skip")
        return None, None

    le = LabelEncoder()
    y_enc = le.fit_transform(y)
    counts = pd.Series(y_enc).value_counts()
    strat = y_enc if counts.min() >= 2 and len(counts) > 1 else None
    X_train, X_test, y_train, y_test = train_test_split(
        X, y_enc, test_size=0.25, random_state=seed, stratify=strat
    )
    class_counts = np.bincount(y_train)
    class_weight = {
        i: (len(y_train) / (len(class_counts) * c))
        for i, c in enumerate(class_counts)
        if c
    }
    sw = np.array([class_weight[i] for i in y_train])

    try:
        model = XGBClassifier(
            n_estimators=250,
            max_depth=6,
            learning_rate=0.08,
            subsample=0.9,
            colsample_bytree=0.9,
            objective="multi:softprob",
            num_class=len(le.classes_),
            eval_metric="mlogloss",
            random_state=seed,
            n_jobs=-1,
        )
        model.fit(X_train, y_train, sample_weight=sw)
        pred = model.predict(X_test)
    except Exception as e:
        print(f"xgboost train failed ({e}) — skip")
        return None, None
    return (model, le), (X_train, X_test, y_train, y_test, pred)


def train_isolation_daily(points: List[dict], out_path: Path) -> Optional[IsolationForest]:
    """Unsupervised slow-theft detector on daily fuel residual per IMEI."""
    by_key: Dict[Tuple[str, str], list] = defaultdict(list)
    for p in points:
        imei = str(p.get("imei") or "").strip()
        t = str(p.get("deviceDataInsertTime") or "")[:10]
        if not imei or len(t) < 10:
            continue
        try:
            fuel = float(p.get("fuelInLiter") or 0)
        except (TypeError, ValueError):
            continue
        by_key[(imei, t)].append(fuel)

    rows = []
    for (imei, day), fuels in by_key.items():
        if len(fuels) < 5:
            continue
        # daily drop = start - end (positive = net loss)
        drop = float(fuels[0] - fuels[-1])
        rows.append({"imei": imei, "day": day, "daily_drop": drop, "n": len(fuels)})
    if len(rows) < 30:
        print("IsolationForest: not enough daily rows")
        return None

    daily = pd.DataFrame(rows)
    # per-imei residual vs vehicle median drop
    daily["imei_median"] = daily.groupby("imei")["daily_drop"].transform("median")
    daily["residual"] = daily["daily_drop"] - daily["imei_median"]
    X = daily[["daily_drop", "residual", "n"]].values
    iso = IsolationForest(n_estimators=200, contamination=0.05, random_state=42)
    daily["anomaly"] = iso.fit_predict(X)
    daily["anomaly_score"] = iso.decision_function(X)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    daily.to_csv(out_path, index=False)
    print(f"IsolationForest daily residuals → {out_path} ({(daily['anomaly']==-1).sum()} anomalies)")
    joblib.dump(iso, out_path.with_suffix(".pkl"))
    return iso


def main():
    parser = argparse.ArgumentParser(description="Train session-level fuel event classifier")
    parser.add_argument(
        "--data",
        default=str(ROOT / "fuel_data" / "june_2026_data.json"),
        help="June telemetry JSON",
    )
    parser.add_argument(
        "--verified",
        default=str(ROOT / "fuel_data" / "verified_theft_events.json"),
    )
    parser.add_argument("--out-dir", default=str(ROOT / "models"))
    parser.add_argument("--max-points", type=int, default=None, help="Debug: truncate input")
    parser.add_argument("--skip-isolation", action="store_true")
    parser.add_argument("--workers", type=int, default=8, help="Parallel IMEI workers")
    parser.add_argument(
        "--stride",
        type=int,
        default=2,
        help="Keep every Nth tick when building sessions (2=half resolution, still full June)",
    )
    parser.add_argument(
        "--sessions-cache",
        default=str(ROOT / "models" / "june_sessions_cache.pkl"),
        help="Pickle cache of built sessions (skip rebuild if present)",
    )
    parser.add_argument("--rebuild-sessions", action="store_true")
    parser.add_argument("--xgboost", action="store_true", help="Also train XGBoost if available")
    args = parser.parse_args()

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    cache = Path(args.sessions_cache)
    if args.rebuild_sessions and cache.exists():
        cache.unlink()

    frame = build_training_frame(
        Path(args.data),
        Path(args.verified),
        args.max_points,
        workers=args.workers,
        stride=args.stride,
        sessions_cache=cache,
    )
    sessions_path = out_dir / "june_session_training_rows.csv"
    frame.to_csv(sessions_path, index=False)
    print(f"Wrote session rows → {sessions_path}")

    # Downsample abundant noise so RF sees minority classes
    noise = frame[frame["label"] == "noise"]
    other = frame[frame["label"] != "noise"]
    if len(noise) > max(2000, 3 * len(other)):
        noise = noise.sample(n=max(2000, 3 * len(other)), random_state=42)
        frame = pd.concat([other, noise], ignore_index=True)
        print(f"Downsampled noise → training rows={len(frame)}")
        print(frame["label"].value_counts().to_string())

    X = frame[FEATURE_COLS].fillna(0)
    y = frame["label"].values

    print("\n=== RandomForest (baseline) ===")
    rf, X_train, X_test, y_train, y_test, pred = train_random_forest(X, y)
    labels_present = sorted(set(y_test) | set(pred))
    print("Confusion matrix:")
    print(confusion_matrix(y_test, pred, labels=labels_present))
    print(classification_report(y_test, pred, labels=labels_present, zero_division=0))
    imp = pd.Series(rf.feature_importances_, index=FEATURE_COLS).sort_values(ascending=False)
    print("Feature importance:")
    print(imp.to_string())
    bundle = {
        "model": rf,
        "features": FEATURE_COLS,
        "labels": LABELS,
        "model_type": "RandomForestClassifier",
        "trained_at": datetime.now().isoformat(timespec="seconds"),
        "n_sessions": len(frame),
        "label_counts": frame["label"].value_counts().to_dict(),
    }
    rf_path = out_dir / "fuel_session_rf.pkl"
    joblib.dump(bundle, rf_path)
    print(f"Saved {rf_path}")

    if args.xgboost:
        print("\n=== XGBoost ===")
        xgb_pack, split = train_xgboost(X, y)
        if xgb_pack is None:
            print("xgboost not installed — skip")
        else:
            (model, le), (X_train, X_test, y_train, y_test, pred) = xgb_pack, split
            names = list(le.classes_)
            print(classification_report(y_test, pred, target_names=names, zero_division=0))
            joblib.dump(
                {
                    "model": model,
                    "label_encoder": le,
                    "features": FEATURE_COLS,
                    "model_type": "XGBClassifier",
                    "trained_at": datetime.now().isoformat(timespec="seconds"),
                },
                out_dir / "fuel_session_xgb.pkl",
            )
            print(f"Saved {out_dir / 'fuel_session_xgb.pkl'}")

    if not args.skip_isolation:
        print("\n=== IsolationForest (daily residual) ===")
        points = _load_json(Path(args.data))
        if args.max_points:
            points = points[: args.max_points]
        train_isolation_daily(points, out_dir / "june_daily_residual_anomalies.csv")

    print("\nDone.")


if __name__ == "__main__":
    main()
