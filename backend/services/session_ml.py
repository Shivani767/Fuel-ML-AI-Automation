"""Session-level RandomForest scoring for ambiguous fill/theft sessions.

Loads models/fuel_session_rf.pkl (from tools/train_session_classifier.py).
Used to promote/demote MANUAL_REVIEW and strong LIKELY_NOISE cases.
"""

from __future__ import annotations

import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from backend import config

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

_lock = threading.Lock()
_bundle = None
_load_attempted = False


def model_path() -> Path:
    return Path(
        os_environ_path(
            "SESSION_RF_PATH",
            config.PROJECT_ROOT / "models" / "fuel_session_rf.pkl",
        )
    )


def os_environ_path(key: str, default: Path) -> Path:
    import os

    raw = os.environ.get(key)
    return Path(raw) if raw else default


def load_model(force: bool = False):
    global _bundle, _load_attempted
    with _lock:
        if _bundle is not None and not force:
            return _bundle
        if _load_attempted and not force:
            return None
        _load_attempted = True
        path = model_path()
        if not path.exists():
            print(f"[session_ml] model not found: {path}")
            return None
        try:
            import joblib

            _bundle = joblib.load(path)
            feats = _bundle.get("features") or FEATURE_COLS
            print(f"[session_ml] loaded {path.name} features={len(feats)}")
            return _bundle
        except Exception as e:
            print(f"[session_ml] failed to load model: {e}")
            _bundle = None
            return None


def _as_bool01(value) -> int:
    if value is None:
        return 0
    if isinstance(value, (int, float, np.integer, np.floating)):
        try:
            if np.isnan(float(value)):
                return 0
        except (TypeError, ValueError):
            pass
        return 1 if float(value) else 0
    if isinstance(value, str):
        return 1 if value.strip().lower() in ("1", "true", "yes", "on") else 0
    return 1 if bool(value) else 0


def _parse_ts(value) -> Optional[datetime]:
    if value is None or value == "":
        return None
    try:
        ts = value.to_pydatetime() if hasattr(value, "to_pydatetime") else value
        if isinstance(ts, datetime):
            return ts.replace(tzinfo=None) if ts.tzinfo else ts
        return datetime.fromisoformat(str(value)[:19])
    except Exception:
        return None


def features_from_session(row: Any) -> Dict[str, float]:
    """Build model feature dict from a v3 session row (Series or mapping)."""
    get = row.get if hasattr(row, "get") else lambda k, d=None: row[k] if k in row else d
    ign = str(get("ignition_status") or get("ignition") or "").lower()
    st = _parse_ts(get("start_time") or get("timestamp"))
    etype = str(get("type") or get("event_type") or "").upper()
    mag = float(get("magnitude") or 0)
    raw = float(get("raw_magnitude") or mag)
    return {
        "magnitude": mag,
        "raw_magnitude": raw,
        "residual_magnitude": float(get("residual_magnitude") or mag),
        "expected_burn_liters": float(get("expected_burn_liters") or 0),
        "duration_minutes": float(get("duration_minutes") or 0),
        "num_ticks": float(get("num_ticks") or 0),
        "ignition_off": 1.0 if ign in ("off", "0", "false") else 0.0,
        "truly_parked": float(_as_bool01(get("truly_parked"))),
        "bounced_back": float(_as_bool01(get("bounced_back"))),
        "plateau_stable": float(_as_bool01(get("plateau_stable"))),
        "channel_disagreement": float(_as_bool01(get("channel_disagreement"))),
        "tamper_flag": float(_as_bool01(get("tamper_flag"))),
        "oscillation": float(_as_bool01(get("oscillation"))),
        "low_internal_voltage": float(_as_bool01(get("low_internal_voltage"))),
        "speed_kmh": float(get("speed_kmh") or 0),
        "hour_of_day": float(st.hour if st else 0),
        "day_of_week": float(st.weekday() if st else 0),
        "is_drain": 1.0 if etype == "DRAIN" else 0.0,
        "vehicle_type_code": 0.0,
    }


def predict_session(row: Any) -> Tuple[Optional[str], float, Dict[str, float]]:
    """Return (label, confidence, proba_by_label)."""
    bundle = load_model()
    if not bundle:
        return None, 0.0, {}
    model = bundle.get("model")
    feats = list(bundle.get("features") or FEATURE_COLS)
    if model is None:
        return None, 0.0, {}
    feat_map = features_from_session(row)
    try:
        import pandas as pd

        x = pd.DataFrame(
            [{c: float(feat_map.get(c, 0.0) or 0.0) for c in feats}],
            columns=feats,
        )
        label = str(model.predict(x)[0])
        proba = {}
        if hasattr(model, "predict_proba"):
            probs = model.predict_proba(x)[0]
            classes = list(getattr(model, "classes_", []))
            proba = {str(c): float(p) for c, p in zip(classes, probs)}
            conf = float(proba.get(label, max(proba.values()) if proba else 0.0))
        else:
            conf = 0.55
        return label, conf, proba
    except Exception:
        return None, 0.0, {}


def apply_ml_to_session_decision(row: Dict[str, Any]) -> Dict[str, Any]:
    """Mutate/copy session mapping: set ml_* and optionally adjust decision."""
    out = dict(row)
    label, conf, proba = predict_session(out)
    out["ml_label"] = label
    out["ml_confidence"] = round(conf, 3) if label else None
    out["ml_proba"] = {k: round(v, 3) for k, v in proba.items()} if proba else None

    if not label:
        return out

    decision = str(out.get("decision") or "")
    etype = str(out.get("type") or out.get("event_type") or "").upper()
    want = "theft" if etype == "DRAIN" else "fill" if etype == "FILL" else None

    # Promote strong LIKELY_NOISE that RF agrees is a real event
    if decision == "LIKELY_NOISE" and want and label == want and conf >= 0.65:
        out["decision"] = "MANUAL_REVIEW"
        out["ml_action"] = "promote_noise"
        out["confidence"] = max(float(out.get("confidence") or 0), min(0.69, 0.4 + conf * 0.3))
        return out

    # Demote MANUAL_REVIEW that RF calls noise/fault
    if decision == "MANUAL_REVIEW" and label in ("noise", "sensor_fault") and conf >= 0.60:
        out["decision"] = "LIKELY_NOISE"
        out["ml_action"] = "demote_review"
        return out

    # Boost MANUAL_REVIEW when RF agrees with direction
    if decision == "MANUAL_REVIEW" and want and label == want and conf >= 0.55:
        out["confidence"] = max(float(out.get("confidence") or 0), min(0.85, 0.45 + conf * 0.4))
        if conf >= 0.80 and float(out.get("magnitude") or 0) >= 5:
            out["decision"] = "AUTO_CONFIRMED"
            out["ml_action"] = "upgrade_auto"
        else:
            out["ml_action"] = "confirm_review"
        return out

    # Soft-check AUTO_CONFIRMED: if RF strongly disagrees, mark for review
    if decision == "AUTO_CONFIRMED" and label in ("noise", "sensor_fault") and conf >= 0.75:
        out["decision"] = "MANUAL_REVIEW"
        out["ml_action"] = "downgrade_auto"
        return out

    out["ml_action"] = "none"
    return out


def daily_anomalies_for_imei(
    imei: str, from_date: Optional[datetime] = None, to_date: Optional[datetime] = None
) -> List[Dict[str, Any]]:
    """Read IsolationForest CSV flags for an IMEI (June training artifact)."""
    path = config.PROJECT_ROOT / "models" / "june_daily_residual_anomalies.csv"
    if not path.exists() or not imei:
        return []
    try:
        import pandas as pd

        df = pd.read_csv(path)
    except Exception:
        return []
    if df.empty or "imei" not in df.columns:
        return []
    work = df[df["imei"].astype(str) == str(imei)]
    if "anomaly" in work.columns:
        work = work[work["anomaly"] == -1]
    out = []
    for _, r in work.iterrows():
        day = str(r.get("day") or "")[:10]
        if from_date and day and day < from_date.strftime("%Y-%m-%d"):
            continue
        if to_date and day and day > to_date.strftime("%Y-%m-%d"):
            continue
        out.append(
            {
                "day": day,
                "dailyDrop": round(float(r.get("daily_drop") or 0), 2),
                "residual": round(float(r.get("residual") or 0), 2),
                "anomalyScore": round(float(r.get("anomaly_score") or 0), 4),
                "source": "isolation_forest_daily",
            }
        )
    return out
