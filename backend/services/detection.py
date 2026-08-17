"""Detect fuel FILL / THEFT for any device date range (including all-time).

Implements the consolidated session method:
  - Day-by-day full resolution (no stride downsampling)
  - Session consolidation via detector v3 (same direction + location + gap)
  - Magnitude from session start/end levels (never per-tick delta fields)
  - Keep AUTO_CONFIRMED + MANUAL_REVIEW; drop LIKELY_NOISE
  - Always merge verified spreadsheet cases (any size) when vehicle/date match
  - Bounce / stair-step cleanup for sensor faults
"""

from __future__ import annotations

import json
import math
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

from backend import config
from backend.services import data_store
from backend.services import session_ml
from backend.services.metrics import haversine_m as _haversine_m
from backend.services.vehicle_aliases import names_for_device, spreadsheet_to_device

# Method defaults: noise floor 2L; UI may raise floors via query params
DEFAULT_THEFT_MIN_L = 2.0
DEFAULT_FILL_MIN_L = 2.0


def _ensure_detector_path():
    det = str(config.PROJECT_ROOT / "fuel-theft-detector")
    if det not in sys.path:
        sys.path.insert(0, det)


def _points_to_df(points: List[dict], imei: str) -> pd.DataFrame:
    rows = []
    for p in points:
        try:
            ts = datetime.fromisoformat(str(p["time"]))
        except Exception:
            continue
        ignition = "on" if int(p.get("iO_1", 0) or 0) == 1 else "off"
        movement = bool(int(p.get("iO_2", 0) or p.get("iO_6", 0) or 0))
        speed = float(p.get("speed") or 0)
        # Engine-hours proxy from consecutive gaps filled later in residual model
        rows.append(
            {
                "timestamp": ts,
                "imei": imei or str(p.get("imei") or ""),
                "vehicle_name_raw": p.get("deviceName"),
                "fuel_level": float(p.get("fuelInLiter") or 0),
                "fuel_level_edc": float(p.get("fuelEDC") or 0) if p.get("fuelEDC") is not None else None,
                "ignition": ignition,
                "movement": movement,
                "speed_kmh": speed,
                "external_voltage": float(p.get("externalVoltage") or 0) or None,
                "internal_voltage": float(p.get("internalVoltage") or 0) or None,
                "engine_hours_delta": 0.0,
                "distance_km": 0.0,
                "parked_since": None,
                "soft_alert": None,
                "latitude": float(p["latitude"]) if p.get("latitude") not in (None, "") else None,
                "longitude": float(p["longitude"]) if p.get("longitude") not in (None, "") else None,
            }
        )
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows).sort_values("timestamp").reset_index(drop=True)
    # Approximate engine-hours delta from inter-tick time when ignition was on
    if len(df) > 1:
        dt_h = df["timestamp"].diff().dt.total_seconds().fillna(0) / 3600.0
        dt_h = dt_h.clip(lower=0, upper=1.0)  # ignore gaps >1h as one tick
        prev_on = df["ignition"].shift(1).isin(["on"])
        df["engine_hours_delta"] = (dt_h * prev_on.astype(float)).fillna(0.0)
    return df


def _heuristic_events(
    points: List[dict],
    *,
    theft_min: float,
    fill_min: float,
) -> List[Dict[str, Any]]:
    """Fallback only if v3 unavailable: parked large single-step jumps."""
    events = []
    prev = None
    for p in points:
        if prev is None:
            prev = p
            continue
        try:
            delta = float(p.get("fuelInLiter") or 0) - float(prev.get("fuelInLiter") or 0)
        except (TypeError, ValueError):
            prev = p
            continue
        ign = int(prev.get("iO_1", 0) or 0)
        speed = float(prev.get("speed") or 0)
        movement = bool(int(prev.get("iO_2", 0) or prev.get("iO_6", 0) or 0))
        parked = ign == 0 and speed < 1.0 and not movement
        if delta >= fill_min and parked:
            events.append(
                {
                    "eventType": "Fuel Filling",
                    "eventTime": p.get("time"),
                    "volume": round(delta, 2),
                    "confidence": 0.55,
                    "decision": "MANUAL_REVIEW",
                    "source": "heuristic",
                    "ignition": "Off",
                }
            )
        elif delta <= -theft_min and parked:
            events.append(
                {
                    "eventType": "Fuel Theft",
                    "eventTime": p.get("time"),
                    "volume": round(abs(delta), 2),
                    "confidence": 0.55,
                    "decision": "MANUAL_REVIEW",
                    "source": "heuristic",
                    "ignition": "Off",
                }
            )
        prev = p
    return events


def _keep_session_row(row, *, theft_min: float, fill_min: float) -> Optional[Dict[str, Any]]:
    """Map v3 (+ optional RF) session → UI event. Report every qualifying session."""
    # Apply session RF before keep/drop decisions
    scored = session_ml.apply_ml_to_session_decision(
        row.to_dict() if hasattr(row, "to_dict") else dict(row)
    )
    etype = str(scored.get("type") or scored.get("event_type") or "").upper()
    if etype not in ("FILL", "DRAIN"):
        return None
    decision = str(scored.get("decision") or "")
    if decision in ("LIKELY_NOISE", "IGNORED_NOISE"):
        return None
    if decision not in ("AUTO_CONFIRMED", "MANUAL_REVIEW"):
        return None

    mag = float(scored.get("magnitude") or 0)
    raw = float(scored.get("raw_magnitude") or mag)
    event_type = "Fuel Filling" if etype == "FILL" else "Fuel Theft"
    floor = fill_min if event_type == "Fuel Filling" else theft_min
    if mag < floor and raw < floor:
        return None

    ml_label = scored.get("ml_label")
    ml_conf = scored.get("ml_confidence")
    ml_action = scored.get("ml_action")
    source = "detector_v3"
    if ml_action and ml_action not in ("none", None):
        source = f"detector_v3+ml:{ml_action}"

    return {
        "eventType": event_type,
        "eventTime": str(scored.get("start_time") or ""),
        "endTime": str(scored.get("end_time") or ""),
        "volume": round(mag if mag >= floor else raw, 2),
        "rawVolume": round(raw, 2),
        "expectedBurn": float(scored.get("expected_burn_liters") or 0) or None,
        "confidence": float(scored.get("confidence") or 0),
        "decision": decision,
        "source": source,
        "mlLabel": ml_label,
        "mlConfidence": ml_conf,
        "mlAction": ml_action,
        "ignition": str(scored.get("ignition") or scored.get("ignition_status") or ""),
        "speed": float(scored.get("speed_kmh") or 0),
        "durationMinutes": float(scored.get("duration_minutes") or 0) or None,
        "tamper": bool(scored.get("tamper_flag")),
        "trulyParked": bool(scored.get("truly_parked")),
        "latitude": scored.get("latitude") if scored.get("latitude") is not None else scored.get("lat"),
        "longitude": scored.get("longitude") if scored.get("longitude") is not None else scored.get("lon"),
    }


def _event_ts(e: Dict[str, Any]) -> Optional[datetime]:
    raw = str(e.get("eventTime") or "")
    for n in (19, 16, 10):
        try:
            return datetime.fromisoformat(raw[:n])
        except Exception:
            continue
    return None


def _suppress_fill_theft_bounces(events: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Drop FILL↔THEFT pairs of similar volume within 90 minutes (sensor glitch)."""
    if len(events) < 2:
        return events
    keep = [True] * len(events)
    for i, e in enumerate(events):
        if not keep[i] or e.get("decision") == "VERIFIED":
            continue
        ti = _event_ts(e)
        if ti is None:
            continue
        vi = float(e.get("volume") or 0)
        if vi <= 0:
            continue
        for j in range(i + 1, len(events)):
            if not keep[j] or events[j].get("decision") == "VERIFIED":
                continue
            o = events[j]
            if {e.get("eventType"), o.get("eventType")} != {"Fuel Filling", "Fuel Theft"}:
                continue
            tj = _event_ts(o)
            if tj is None or abs((tj - ti).total_seconds()) > 90 * 60:
                continue
            vj = float(o.get("volume") or 0)
            if vj <= 0 or abs(vi - vj) / max(vi, vj) > 0.35:
                continue
            keep[i] = False
            keep[j] = False
    return [e for i, e in enumerate(events) if keep[i]]


def _suppress_tilt_settling(events: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Cancel a THEFT/FILL pair that gives the fuel back at the spot it left it.

    A vehicle parked on a gradient settles fuel away from the sender: the level steps
    down on parking and returns when it drives off, often many hours later. Nothing
    ever left the tank. `_suppress_fill_theft_bounces` cannot see this because its
    window is 90 minutes, so match on *place* as well as time -- settling reverses
    exactly where it began, while a theft followed by a genuine refuel almost always
    happens at a pump somewhere else. Missing coordinates mean no cancellation: a
    blind match here would silently drop real thefts.
    """
    if len(events) < 2:
        return events
    keep = [True] * len(events)
    window_s = config.SETTLING_MATCH_HOURS * 3600.0
    for i, drain in enumerate(events):
        if not keep[i] or drain.get("decision") == "VERIFIED":
            continue
        if drain.get("eventType") != "Fuel Theft":
            continue
        ti = _event_ts(drain)
        vi = float(drain.get("volume") or 0)
        if ti is None or vi <= 0:
            continue
        for j, fill in enumerate(events):
            if j == i or not keep[j] or fill.get("decision") == "VERIFIED":
                continue
            if fill.get("eventType") != "Fuel Filling":
                continue
            tj = _event_ts(fill)
            # The return has to come *after* the drop, or it is not a reversal.
            if tj is None or not (0 < (tj - ti).total_seconds() <= window_s):
                continue
            vj = float(fill.get("volume") or 0)
            if vj <= 0 or abs(vi - vj) / max(vi, vj) > config.SETTLING_MATCH_TOLERANCE:
                continue
            dist = _haversine_m(
                drain.get("latitude"), drain.get("longitude"),
                fill.get("latitude"), fill.get("longitude"),
            )
            if dist is None or dist > config.SETTLING_MATCH_RADIUS_M:
                continue
            keep[i] = False
            keep[j] = False
            break
    return [e for k, e in zip(keep, events) if k]


def _suppress_recurring_noise(events: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    if len(events) < 3:
        return events
    keep = [True] * len(events)
    for i, e in enumerate(events):
        if e.get("decision") == "VERIFIED" or not keep[i]:
            continue
        ti = _event_ts(e)
        if ti is None:
            continue
        vol = float(e.get("volume") or 0)
        cluster = [i]
        for j, o in enumerate(events):
            if i == j or not keep[j] or o.get("decision") == "VERIFIED":
                continue
            if o.get("eventType") != e.get("eventType"):
                continue
            tj = _event_ts(o)
            if tj is None or abs((tj - ti).total_seconds()) > 3 * 3600:
                continue
            ov = float(o.get("volume") or 0)
            if vol > 0 and ov > 0 and abs(ov - vol) / max(vol, ov) <= 0.12:
                cluster.append(j)
        if len(cluster) < 3:
            continue
        vols = [float(events[k].get("volume") or 0) for k in cluster]
        median = sorted(vols)[len(vols) // 2]
        best_i = max(cluster, key=lambda k: float(events[k].get("volume") or 0))
        best_v = float(events[best_i].get("volume") or 0)
        if best_v >= max(median * 2.0, median + 40):
            for k in cluster:
                keep[k] = k == best_i
        else:
            for k in cluster:
                keep[k] = False
    return [e for i, e in enumerate(events) if keep[i]]


def _merge_verified_with_detector(events: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    verified = [e for e in events if e.get("decision") == "VERIFIED"]
    others = [e for e in events if e.get("decision") != "VERIFIED"]
    if not verified:
        return events
    used_v = set()
    out = []
    for e in others:
        te = _event_ts(e)
        for vi, v in enumerate(verified):
            if vi in used_v or e.get("eventType") != v.get("eventType"):
                continue
            tv = _event_ts(v)
            if te is None or tv is None or te.date() != tv.date():
                continue
            ve, vv = float(e.get("volume") or 0), float(v.get("volume") or 0)
            if ve <= 0 or vv <= 0 or abs(ve - vv) / max(ve, vv) > 0.25:
                continue
            e = dict(e)
            e["decision"] = "VERIFIED"
            e["source"] = "detector_v3+verified"
            e["verifiedVolume"] = vv
            used_v.add(vi)
            break
        out.append(e)
    for vi, v in enumerate(verified):
        if vi not in used_v:
            out.append(v)
    return out


def _dedupe(events: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    seen = set()
    out = []
    ranked = sorted(
        events,
        key=lambda x: (
            0 if x.get("decision") == "VERIFIED" else 1,
            -float(x.get("volume") or 0),
            str(x.get("eventTime") or ""),
        ),
    )
    for e in ranked:
        if e.get("decision") == "VERIFIED":
            key = (
                e.get("eventType"),
                str(e.get("eventTime") or "")[:10],
                round(float(e.get("volume") or 0), 1),
                str(e.get("spreadsheetName") or ""),
            )
            if key in seen:
                continue
            seen.add(key)
            out.append(e)
            continue
        minute_key = str(e.get("eventTime") or "")[:16]
        key = (e.get("eventType"), minute_key, round(float(e.get("volume") or 0), 0))
        if key in seen:
            continue
        seen.add(key)
        out.append(e)
    return sorted(out, key=lambda x: str(x.get("eventTime") or ""))


def _load_verified_events() -> List[dict]:
    path = Path(config.FUEL_DATA_DIR) / "verified_theft_events.json"
    if not path.exists():
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _detect_window(
    device_name: str,
    points: List[dict],
    *,
    theft_min: float,
    fill_min: float,
) -> List[Dict[str, Any]]:
    if not points or len(points) < 5:
        return []
    imei = str(points[0].get("imei") or data_store.resolve_imei(device_name) or device_name)
    try:
        _ensure_detector_path()
        from detection.pipeline_v3 import run_session_pipeline

        df = _points_to_df(points, imei=imei)
        if df.empty or len(df) < 5:
            return _heuristic_events(points, theft_min=theft_min, fill_min=fill_min)
        _, ev = run_session_pipeline(df)
        if ev is None or ev.empty:
            return _heuristic_events(points, theft_min=theft_min, fill_min=fill_min)
        events = []
        for _, row in ev.iterrows():
            item = _keep_session_row(row, theft_min=theft_min, fill_min=fill_min)
            if item:
                events.append(item)
        return events
    except Exception:
        return _heuristic_events(points, theft_min=theft_min, fill_min=fill_min)


def _split_by_day(points: List[dict]) -> List[Tuple[str, List[dict]]]:
    buckets: Dict[str, List[dict]] = {}
    for p in points:
        day = str(p.get("time") or "")[:10]
        if len(day) < 10:
            continue
        buckets.setdefault(day, []).append(p)
    return sorted(buckets.items(), key=lambda x: x[0])


def detect_fill_theft(
    device_name: str,
    points: List[dict],
    *,
    min_liters: Optional[float] = None,
    theft_min: Optional[float] = None,
    fill_min: Optional[float] = None,
    include_stored_noise: bool = False,
) -> List[Dict[str, Any]]:
    """Run session detector for FILL / THEFT on any From–To (including all-time)."""
    if not points:
        return []

    theft_min = float(theft_min if theft_min is not None else (min_liters or DEFAULT_THEFT_MIN_L))
    fill_min = float(fill_min if fill_min is not None else (min_liters or DEFAULT_FILL_MIN_L))
    theft_min = max(2.0, theft_min)
    fill_min = max(2.0, fill_min)

    # Always day-by-day for multi-day / all-time windows
    try:
        t0 = datetime.fromisoformat(str(points[0]["time"]))
        t1 = datetime.fromisoformat(str(points[-1]["time"]))
        multi_day = (t1.date() - t0.date()).days >= 1 or len(points) > 8000
    except Exception:
        multi_day = len(points) > 8000

    events: List[Dict[str, Any]] = []
    if multi_day:
        for _day, day_pts in _split_by_day(points):
            events.extend(
                _detect_window(device_name, day_pts, theft_min=theft_min, fill_min=fill_min)
            )
    else:
        events = _detect_window(device_name, points, theft_min=theft_min, fill_min=fill_min)

    try:
        t0 = datetime.fromisoformat(str(points[0]["time"]))
        t1 = datetime.fromisoformat(str(points[-1]["time"]))
    except Exception:
        return _dedupe(
            _merge_verified_with_detector(
                _suppress_tilt_settling(
                    _suppress_fill_theft_bounces(_suppress_recurring_noise(events))
                )
            )
        )

    allowed_names = {n.lower() for n in names_for_device(device_name)}
    allowed_names.add(device_name.strip().lower())
    allowed_names.add(spreadsheet_to_device(device_name).strip().lower())

    for e in _load_verified_events():
        name = str(e.get("deviceName") or "").strip()
        mapped = spreadsheet_to_device(name).strip().lower()
        if name.lower() not in allowed_names and mapped not in allowed_names:
            continue
        try:
            et = datetime.fromisoformat(str(e.get("eventTime"))[:19])
        except Exception:
            try:
                et = datetime.fromisoformat(str(e.get("eventTime"))[:10])
            except Exception:
                continue
        if et.date() < t0.date() or et.date() > t1.date():
            continue
        vol = float(e.get("fuelDecrease") or e.get("fuelIncrease") or 0)
        if abs(vol) < 0.5:
            continue
        events.append(
            {
                "eventType": e.get("eventType") or "Fuel Theft",
                "eventTime": e.get("eventTime"),
                "volume": round(abs(vol), 2),
                "confidence": 0.95,
                "decision": "VERIFIED",
                "source": "verified_theft_events",
                "spreadsheetName": name,
                "ignition": e.get("ignitionStatus") or "OFF",
            }
        )

    if include_stored_noise:
        for e in data_store.load_events():
            if str(e.get("deviceName") or "").strip().lower() != device_name.strip().lower():
                continue
            try:
                et = datetime.fromisoformat(str(e.get("eventTime")))
            except Exception:
                continue
            if et < t0 or et > t1:
                continue
            vol = float(e.get("fuelIncrease") or e.get("fuelDecrease") or 0)
            events.append(
                {
                    "eventType": e.get("eventType"),
                    "eventTime": e.get("eventTime"),
                    "volume": round(abs(vol), 2),
                    "confidence": 0.4,
                    "decision": "STORED",
                    "source": "fuel_events",
                }
            )

    cleaned = _suppress_fill_theft_bounces(_suppress_recurring_noise(events))
    cleaned = _suppress_tilt_settling(cleaned)
    cleaned = _merge_verified_with_detector(cleaned)
    return _dedupe(cleaned)
