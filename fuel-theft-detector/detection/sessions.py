"""Session consolidation — group continuous same-direction, same-location ticks.

Root cause this fixes: classifying every polling tick as its own event so one
continuous drain is logged as ~20 rows. Magnitude is computed from boundary
fuel levels, never from unreliable per-tick delta fields.
"""

from __future__ import annotations

from typing import List, Optional

import numpy as np
import pandas as pd

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import DetectorConfig, DEFAULT_CONFIG


def haversine_meters(lat1, lon1, lat2, lon2) -> float:
    R = 6371000.0
    p1, p2 = np.radians(float(lat1)), np.radians(float(lat2))
    dphi = np.radians(float(lat2) - float(lat1))
    dlambda = np.radians(float(lon2) - float(lon1))
    a = np.sin(dphi / 2) ** 2 + np.cos(p1) * np.cos(p2) * np.sin(dlambda / 2) ** 2
    return float(R * 2 * np.arcsin(np.sqrt(min(a, 1.0))))


def same_location(c1: dict, c2: dict, radius_m: float) -> bool:
    """True if within radius. Missing lat/lon → do not block grouping."""
    lat1, lon1 = c1.get("lat"), c1.get("lon")
    lat2, lon2 = c2.get("lat"), c2.get("lon")
    if None in (lat1, lon1, lat2, lon2):
        return True
    try:
        return haversine_meters(lat1, lon1, lat2, lon2) <= radius_m
    except (TypeError, ValueError):
        return True


def is_truly_stationary(row) -> bool:
    """GPS speed AND accelerometer movement both required."""
    speed = row.get("speed_kmh", None) if hasattr(row, "get") else None
    movement = row.get("movement", None) if hasattr(row, "get") else None
    if speed is None or movement is None or (isinstance(speed, float) and np.isnan(speed)):
        return False
    return float(speed) == 0 and bool(movement) is False


def _finalize_session(session: dict) -> dict:
    session["magnitude"] = round(abs(session["end_level"] - session["start_level"]), 2)
    session["quantity_liters"] = session["magnitude"]
    session["duration_minutes"] = round(
        (session["end_time"] - session["start_time"]).total_seconds() / 60.0, 1
    )
    session["event_type"] = session["type"]
    session["timestamp"] = session["end_time"]
    return session


def build_sessions(
    df: pd.DataFrame,
    cfg: Optional[DetectorConfig] = None,
    fuel_col: str = "fuel_primary",
) -> List[dict]:
    """
    Walk smoothed level series; group consecutive same-direction,
    same-location readings into sessions.
    """
    cfg = cfg or DEFAULT_CONFIG
    if df is None or len(df) < 2:
        return []

    work = df.sort_values("timestamp").reset_index(drop=True)
    if fuel_col not in work.columns:
        fuel_col = "fuel_used" if "fuel_used" in work.columns else (
            "fuel_smooth" if "fuel_smooth" in work.columns else "fuel_level"
        )

    sessions: List[dict] = []
    active = None
    max_gap = cfg.session_max_gap_minutes
    radius = cfg.recurrence_location_radius_m

    for i in range(1, len(work)):
        prev, curr = work.iloc[i - 1], work.iloc[i]
        delta = float(curr[fuel_col]) - float(prev[fuel_col])
        if abs(delta) < 0.01:
            continue

        direction = "FILL" if delta > 0 else "DRAIN"
        lat = curr.get("latitude", curr.get("lat"))
        lon = curr.get("longitude", curr.get("lon"))

        if active is None:
            active = {
                "type": direction,
                "start_time": prev["timestamp"],
                "end_time": curr["timestamp"],
                "start_level": float(prev[fuel_col]),
                "end_level": float(curr[fuel_col]),
                "lat": lat,
                "lon": lon,
                "num_ticks": 1,
                "start_idx": i - 1,
                "end_idx": i,
                "imei": str(curr.get("imei", "")),
            }
            continue

        gap_minutes = (curr["timestamp"] - active["end_time"]).total_seconds() / 60.0
        same_dir = direction == active["type"]
        same_loc = same_location(
            {"lat": active["lat"], "lon": active["lon"]},
            {"lat": lat, "lon": lon},
            radius,
        )

        if same_dir and same_loc and gap_minutes <= max_gap:
            active["end_time"] = curr["timestamp"]
            active["end_level"] = float(curr[fuel_col])
            active["num_ticks"] += 1
            active["end_idx"] = i
            active["lat"] = lat if lat is not None else active["lat"]
            active["lon"] = lon if lon is not None else active["lon"]
        else:
            sessions.append(_finalize_session(active))
            active = {
                "type": direction,
                "start_time": prev["timestamp"],
                "end_time": curr["timestamp"],
                "start_level": float(prev[fuel_col]),
                "end_level": float(curr[fuel_col]),
                "lat": lat,
                "lon": lon,
                "num_ticks": 1,
                "start_idx": i - 1,
                "end_idx": i,
                "imei": str(curr.get("imei", "")),
            }

    if active is not None:
        sessions.append(_finalize_session(active))
    return merge_split_sessions(sessions, cfg)


def merge_split_sessions(
    sessions: List[dict],
    cfg: Optional[DetectorConfig] = None,
) -> List[dict]:
    """Rejoin one event that noise split into consecutive fragments.

    A session closes on any direction change, so a momentary tick in the opposite
    direction cuts a continuous event in half. Observed on a confirmed 204 L theft:
    a single 0.5 L blip at 11:24 split it into 87.58 L and 117.33 L fragments 30
    seconds apart, each landing in MANUAL_REVIEW instead of one AUTO_CONFIRMED event.

    Fragments are rejoined only when the interruption is small in both time and
    volume, so genuinely separate events stay separate.
    """
    cfg = cfg or DEFAULT_CONFIG
    if len(sessions) < 2:
        return sessions

    max_gap = getattr(cfg, "split_merge_max_gap_minutes", 3.0)
    max_rebound = getattr(cfg, "split_merge_max_rebound_liters", 2.0)
    rebound_frac = getattr(cfg, "split_merge_rebound_fraction", 0.05)
    radius = cfg.recurrence_location_radius_m

    ordered = sorted(sessions, key=lambda s: s["start_time"])

    def _absorb(cur: dict, nxt: dict, bridged: int = 0) -> None:
        cur["end_time"] = nxt["end_time"]
        cur["end_level"] = nxt["end_level"]
        cur["end_idx"] = nxt.get("end_idx", cur.get("end_idx"))
        cur["num_ticks"] = cur.get("num_ticks", 1) + nxt.get("num_ticks", 1)
        cur["lat"] = nxt.get("lat", cur.get("lat"))
        cur["lon"] = nxt.get("lon", cur.get("lon"))
        cur["merged_fragments"] = cur.get("merged_fragments", 1) + 1 + bridged
        _finalize_session(cur)

    def _joinable(cur: dict, nxt: dict, gap: float) -> bool:
        if nxt["type"] != cur["type"] or not (0 <= gap <= max_gap):
            return False
        if not same_location(cur, nxt, radius):
            return False
        rebound = abs(float(nxt["start_level"]) - float(cur["end_level"]))
        combined = abs(float(nxt["end_level"]) - float(cur["start_level"]))
        return rebound <= max(max_rebound, combined * rebound_frac)

    out: List[dict] = []
    i = 0
    while i < len(ordered):
        cur = ordered[i]
        i += 1
        while i < len(ordered):
            nxt = ordered[i]
            gap = (nxt["start_time"] - cur["end_time"]).total_seconds() / 60.0
            if _joinable(cur, nxt, gap):
                _absorb(cur, nxt)
                i += 1
                continue
            # The counter-tick that split the event shows up as its own tiny session
            # between the fragments -- 0.46 L of "fill" inside a 204 L drain. Step
            # over it when it is negligible, but judge it on an absolute cap so a
            # real refuel is never swallowed by a large neighbouring drain.
            after = ordered[i + 1] if i + 1 < len(ordered) else None
            if (
                after is not None
                and nxt["type"] != cur["type"]
                and float(nxt["magnitude"]) <= max_rebound
                and (after["start_time"] - cur["end_time"]).total_seconds() / 60.0 <= max_gap
                and _joinable(cur, after,
                              (after["start_time"] - cur["end_time"]).total_seconds() / 60.0)
            ):
                _absorb(cur, after, bridged=1)
                i += 2
                continue
            break
        out.append(cur)

    return out


def validate_session_shape(
    df: pd.DataFrame,
    session: dict,
    cfg: Optional[DetectorConfig] = None,
    fuel_col: str = "fuel_primary",
) -> dict:
    cfg = cfg or DEFAULT_CONFIG
    # Caller should pass a timestamp-sorted frame; avoid O(n log n) re-sort per session.
    work = df
    if fuel_col not in work.columns:
        fuel_col = "fuel_used" if "fuel_used" in work.columns else "fuel_level"

    end_idx = int(session.get("end_idx") or -1)
    if 0 <= end_idx < len(work) - 1:
        post = work.iloc[end_idx + 1 : end_idx + 1 + cfg.plateau_check_readings]
    else:
        post = work[work["timestamp"] > session["end_time"]].head(cfg.plateau_check_readings)
    if len(post) < 2:
        session["plateau_stable"] = None
        session["plateau_ok"] = None
        session["bounced_back"] = None
        session["bounce_back"] = None
        return session

    session["plateau_stable"] = float(post[fuel_col].std()) <= cfg.plateau_std
    session["plateau_ok"] = session["plateau_stable"]
    bounced = abs(float(post[fuel_col].iloc[-1]) - float(session["start_level"])) <= cfg.bounce_back_tolerance
    session["bounced_back"] = bounced
    session["bounce_back"] = bounced
    return session
