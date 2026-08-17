"""v3 session-first detection pipeline.

Implements the consolidated fuel theft/fill method:
  consolidate same-direction/same-location ticks → magnitude from boundary levels
  → shape checks → ignition-on residual → confidence tiers.
Never classifies a single tick alone; never trusts a pre-computed delta field.
"""

from __future__ import annotations

from typing import Optional, Tuple

import numpy as np
import pandas as pd

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
from config import DetectorConfig, DEFAULT_CONFIG
from detection import mass_balance
from data.vehicle_assignments import attach_vehicle_names, load_assignments, resolve_vehicle
from detection.smoothing import smooth_telemetry
from detection.tamper import attach_voltage_flags, tamper_near
from detection.sessions import (
    build_sessions,
    validate_session_shape,
    is_truly_stationary,
)


def _session_window(
    prepared: pd.DataFrame,
    session: dict,
    *,
    include_start: bool = False,
) -> pd.DataFrame:
    """The ticks a session actually covers.

    `build_sessions` records `start_idx` as the last reading *before* the level moved,
    so the vehicle's state during the event lives in start_idx+1 .. end_idx. Reading
    the boundary tick instead describes the vehicle as it was beforehand: for a drain
    that begins the moment the vehicle parks, that reads as "driving, ignition on",
    which loses the ignition-off and parked credits and bills engine burn against a
    stationary vehicle. Pass include_start=True when the pre-change level is wanted.
    """
    if prepared is None or len(prepared) == 0:
        return prepared
    start_idx = int(session.get("start_idx") or 0)
    end_idx = int(session.get("end_idx") or start_idx)
    lo = start_idx if include_start else min(start_idx + 1, end_idx)
    if 0 <= lo <= end_idx < len(prepared):
        return prepared.iloc[lo : end_idx + 1]
    return prepared[
        (prepared["timestamp"] >= session["start_time"])
        & (prepared["timestamp"] <= session["end_time"])
    ]


def _tick_engine_on(row) -> bool:
    """Engine state for one tick, preferring the derived signal over raw iO_1.

    Ignition is stuck off on some devices even while they drive -- one logged 1,015
    moving ticks and 119.8 km with iO_1 == 0 for a whole day.
    """
    val = row.get("engine_running")
    if val is not None and not (isinstance(val, float) and np.isnan(val)):
        return bool(val)
    if str(row.get("ignition", "")).lower() in ("on", "1", "true"):
        return True
    try:
        return float(row.get("speed_kmh") or 0) > 0
    except (TypeError, ValueError):
        return False


def _integrate_expected_burn(
    prepared: pd.DataFrame,
    session: dict,
    cfg: DetectorConfig,
) -> Tuple[float, float]:
    """(liters, engine_minutes) accumulated interval by interval across the session.

    Billing the whole session at the running rate whenever *any* tick showed motion
    let one spurious speed sample inside a two-hour parked drain subtract
    120 min x 3.5 L/h = 7 L from a genuine theft -- enough to push it under the
    confirmed-above band and out of the alert stream. Each interval is now credited
    at the rate it was actually in, so a single stray tick costs its own ~30 s of
    idle burn and nothing more.
    """
    window = _session_window(prepared, session, include_start=True)
    if window is None or len(window) < 2:
        return 0.0, 0.0
    rates = cfg.rates_for(str(session.get("vehicle_type") or "default"))
    recs = window.to_dict("records")
    liters = 0.0
    engine_minutes = 0.0
    for i in range(len(recs) - 1):
        try:
            dt_min = (recs[i + 1]["timestamp"] - recs[i]["timestamp"]).total_seconds() / 60.0
        except (TypeError, ValueError, KeyError):
            continue
        if dt_min <= 0:
            continue
        # A long silence is not engine time; mirrors the 1 h clamp used upstream.
        dt_min = min(dt_min, 60.0)
        if not _tick_engine_on(recs[i]):
            continue
        engine_minutes += dt_min
        try:
            moving = float(recs[i].get("speed_kmh") or 0) > 1.0
        except (TypeError, ValueError):
            moving = False
        rate = rates.running_burn_rate_lph if moving else rates.idle_burn_rate_lph
        liters += (dt_min / 60.0) * rate * rates.load_factor
    return liters, engine_minutes


def _session_expected_burn(session: dict, cfg: DetectorConfig) -> float:
    """Duration-based fallback for callers without the prepared frame.

    Prefer `_integrate_expected_burn`, which bills each interval at its own rate.
    """
    ign = str(session.get("ignition_status", "")).lower()
    running = session.get("engine_running")
    if running is None:
        running = ign not in ("off", "0", "false", "unknown", "")
    if not running:
        return 0.0
    hours = max(float(session.get("duration_minutes") or 0.0), 0.0) / 60.0
    if hours <= 0:
        return 0.0
    rates = cfg.rates_for(str(session.get("vehicle_type") or "default"))
    if bool(session.get("truly_parked")):
        return hours * rates.idle_burn_rate_lph * rates.load_factor
    return hours * rates.running_burn_rate_lph * rates.load_factor


def _apply_residual_magnitude(session: dict, cfg: DetectorConfig) -> dict:
    """For engine-ON drains, only residual above expected burn counts toward theft."""
    raw = float(session.get("magnitude") or 0.0)
    session["raw_magnitude"] = raw
    expected = session.get("expected_burn_liters")
    if expected is None:
        expected = _session_expected_burn(session, cfg)
        session["expected_burn_liters"] = round(float(expected), 2)
    expected = float(expected or 0.0)
    if session.get("type") == "DRAIN" and expected > 0:
        residual = max(0.0, raw - expected)
        session["residual_magnitude"] = round(residual, 2)
        session["magnitude"] = round(residual, 2)
        session["quantity_liters"] = session["magnitude"]
    else:
        session["residual_magnitude"] = raw
    return session


def _session_state(prepared: pd.DataFrame, session: dict) -> dict:
    """Vehicle state during the session, aggregated over the session's own ticks."""
    window = _session_window(prepared, session)
    if window is None or len(window) == 0:
        return {}
    recs = window.to_dict("records")
    speeds = []
    for r in recs:
        try:
            speeds.append(float(r.get("speed_kmh") or 0))
        except (TypeError, ValueError):
            continue
    ign_on = any(str(r.get("ignition", "")).lower() in ("on", "1", "true") for r in recs)
    first = recs[0]
    return {
        "ignition_status": "on" if ign_on else "off",
        # Parked only if it never moved for the whole event, not merely at one instant.
        "truly_parked": all(is_truly_stationary(r) for r in recs),
        "speed_kmh": max(speeds) if speeds else 0.0,
        "channel_disagreement": any(bool(r.get("channel_disagreement", False)) for r in recs),
        "low_internal_voltage": any(bool(r.get("low_internal_voltage", False)) for r in recs),
        "vehicle_name": first.get("vehicle_name"),
        "vehicle_type": first.get("vehicle_type", "default"),
    }


def _duration_ok(session: dict, cfg: DetectorConfig) -> bool:
    """Real theft/fill resolves in minutes; multi-hour gradual decline is consumption."""
    dur = float(session.get("duration_minutes") or 0.0)
    if dur <= cfg.max_event_duration_minutes:
        return True
    mag = float(session.get("raw_magnitude") or session.get("magnitude") or 0.0)
    # Parked same-location sessions can span >20 min wall-clock (sparse polls /
    # multi-step siphon) — allow up to 2 hours when magnitude clears noise floor.
    if session.get("truly_parked") and mag >= cfg.ignore_below:
        return dur <= 120.0
    # Large parked events even without truly_parked flag (missing GPS)
    if mag >= cfg.confirmed_above * 2 and dur <= 90.0:
        return True
    return False


def _oscillation_flag(prepared: pd.DataFrame, session: dict, fuel_col: str = "fuel_primary") -> bool:
    """Rapid up-down jitter inside the session window → device fault, not theft/fill."""
    if prepared is None or prepared.empty:
        return False
    start_idx = int(session.get("start_idx") or 0)
    end_idx = int(session.get("end_idx") or start_idx)
    if 0 <= start_idx <= end_idx < len(prepared):
        window = prepared.iloc[start_idx : end_idx + 1]
    else:
        window = prepared[
            (prepared["timestamp"] >= session["start_time"])
            & (prepared["timestamp"] <= session["end_time"])
        ]
    if len(window) < 6 or fuel_col not in window.columns:
        return False
    lvl = pd.to_numeric(window[fuel_col], errors="coerce").dropna()
    if len(lvl) < 6:
        return False
    diffs = lvl.diff().dropna()
    if len(diffs) < 4:
        return False
    signs = np.sign(diffs.to_numpy())
    signs = signs[signs != 0]
    if len(signs) < 4:
        return False
    sign_flips = int(np.sum(signs[1:] != signs[:-1]))
    # Many direction flips with small median step → oscillation
    return sign_flips >= 4 and float(np.median(np.abs(diffs.to_numpy()))) < 2.0


def score_session_confidence(session: dict, cfg: Optional[DetectorConfig] = None) -> float:
    cfg = cfg or DEFAULT_CONFIG
    score = 0.0
    qty = float(session.get("magnitude") or 0.0)

    if qty > cfg.confirmed_above:
        score += 0.35
    elif qty >= cfg.ignore_below:
        score += 0.15
    else:
        return 0.0

    if session.get("bounced_back") is False:
        score += 0.20
    if session.get("plateau_stable") is True:
        score += 0.20

    if session.get("type") == "DRAIN":
        # Only credit "ignition off" when ignition is believable on this device.
        # Where iO_1 is stuck off, this bonus would fire on every drain -- including
        # the false ones created by unsubtracted driving burn.
        if (
            str(session.get("ignition_status", "")).lower() == "off"
            and session.get("ignition_trustworthy", True)
            and not session.get("engine_running")
        ):
            score += 0.15
        if session.get("truly_parked"):
            score += 0.05
        if session.get("tamper_flag"):
            score += 0.20

    if session.get("channel_disagreement"):
        score -= 0.15
    if session.get("low_internal_voltage"):
        score -= 0.10
    if session.get("oscillation"):
        score -= 0.25

    return max(0.0, min(round(score, 2), 1.0))


def classify_session(session: dict, cfg: Optional[DetectorConfig] = None) -> str:
    cfg = cfg or DEFAULT_CONFIG
    conf = float(session.get("confidence") or 0.0)
    mag = float(session.get("magnitude") or 0.0)
    if mag < cfg.ignore_below:
        return "IGNORED_NOISE"
    if session.get("discarded_as_glitch"):
        return "LIKELY_NOISE"
    if not session.get("duration_ok", True):
        return "IGNORED_NOISE"
    if session.get("oscillation"):
        return "MANUAL_REVIEW"
    if conf >= cfg.auto_confirmed_min:
        return "AUTO_CONFIRMED"
    if conf >= cfg.manual_review_min:
        return "MANUAL_REVIEW"
    return "LIKELY_NOISE"


def prepare_v3_frame(df: pd.DataFrame, cfg: Optional[DetectorConfig] = None) -> pd.DataFrame:
    cfg = cfg or DEFAULT_CONFIG
    out = attach_vehicle_names(df) if "vehicle_name" not in df.columns else df.copy()
    out = smooth_telemetry(out, cfg)
    if "fuel_used" in out.columns:
        out["fuel_primary"] = out["fuel_used"]
    elif "fuel_smooth" in out.columns:
        out["fuel_primary"] = out["fuel_smooth"]
    else:
        out["fuel_primary"] = out["fuel_level"]
    out = attach_voltage_flags(out, cfg)
    return out


def run_session_pipeline(
    telemetry: pd.DataFrame,
    cfg: Optional[DetectorConfig] = None,
    assignment_path: Optional[str] = None,
) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """
    Returns (prepared_frame, sessions_df).
    sessions_df columns match report/plot expectations.
    """
    cfg = cfg or DEFAULT_CONFIG
    if telemetry is None or telemetry.empty:
        return telemetry, pd.DataFrame()

    prepared = prepare_v3_frame(telemetry, cfg)
    sessions = build_sessions(prepared, cfg)

    # A device whose ignition never reads ON despite logging motion has a broken or
    # unwired iO_1. Decided once per frame so every session on that device agrees.
    ignition_trustworthy = True
    if "ignition" in prepared.columns:
        moving = pd.Series(False, index=prepared.index)
        if "speed_kmh" in prepared.columns:
            moving = pd.to_numeric(prepared["speed_kmh"], errors="coerce").fillna(0) > 3
        n_moving = int(moving.sum())
        n_ign_on = int((prepared["ignition"].astype(str).str.lower() == "on").sum())
        if n_moving >= 20 and n_ign_on == 0:
            ignition_trustworthy = False

    assignments = None
    if assignment_path:
        assignments = load_assignments(assignment_path)
    else:
        try:
            assignments = load_assignments()
        except Exception:
            assignments = None

    rows = []
    for s in sessions:
        s = validate_session_shape(prepared, s, cfg)
        start_idx = int(s.get("start_idx") or 0)
        if 0 <= start_idx < len(prepared):
            nearest = prepared.iloc[start_idx]
        else:
            # Fallback only when indices missing
            nearest_idx = (prepared["timestamp"] - s["start_time"]).abs().to_numpy().argmin()
            nearest = prepared.iloc[int(nearest_idx)]
        s["ignition_trustworthy"] = ignition_trustworthy

        # State comes from the session's own ticks, not the boundary reading before it.
        state = _session_state(prepared, s)
        if state:
            s.update(state)
        elif nearest is not None:
            s["ignition_status"] = nearest.get("ignition", "unknown")
            s["channel_disagreement"] = bool(nearest.get("channel_disagreement", False))
            s["low_internal_voltage"] = bool(nearest.get("low_internal_voltage", False))
            s["truly_parked"] = is_truly_stationary(nearest)
            s["vehicle_name"] = nearest.get("vehicle_name")
            s["vehicle_type"] = nearest.get("vehicle_type", "default")
            s["speed_kmh"] = float(nearest.get("speed_kmh") or 0)
        else:
            s["ignition_status"] = "unknown"
            s["channel_disagreement"] = False
            s["low_internal_voltage"] = False
            s["truly_parked"] = False
            s["speed_kmh"] = 0.0

        # Burn is integrated per interval; vehicle_type must already be resolved above.
        burn_liters, engine_minutes = _integrate_expected_burn(prepared, s, cfg)
        s["engine_minutes"] = round(engine_minutes, 1)
        s["expected_burn_liters"] = round(burn_liters, 2)
        # One stray motion tick inside a long parked drain is not "the engine ran".
        _dur = float(s.get("duration_minutes") or 0.0)
        s["engine_running"] = engine_minutes >= max(1.0, 0.10 * _dur)

        has_tamper, tamper_ts = tamper_near(prepared, s["start_time"], cfg)
        s["tamper_flag"] = has_tamper
        s["tamper_time"] = tamper_ts

        s = _apply_residual_magnitude(s, cfg)
        s["duration_ok"] = _duration_ok(s, cfg)
        s["oscillation"] = _oscillation_flag(prepared, s)

        if assignments is not None and not assignments.empty:
            info = resolve_vehicle(s.get("imei") or prepared["imei"].iloc[0], s["start_time"], assignments)
            if info.get("vehicle_name"):
                s["vehicle_name"] = info["vehicle_name"]
                s["vehicle"] = info["vehicle_name"]
            else:
                s["vehicle"] = s.get("vehicle_name") or "UNKNOWN_VEHICLE"
        else:
            s["vehicle"] = s.get("vehicle_name") or "N/A (no assignment file)"

        s["discarded_as_glitch"] = bool(s.get("bounced_back") is True)
        s["discard_reason"] = "bounce_back_glitch" if s["discarded_as_glitch"] else None
        if not s["duration_ok"]:
            s["discard_reason"] = "duration_exceeds_event_window"
        if s.get("oscillation"):
            s["discard_reason"] = "oscillation_jitter"

        s["confidence"] = score_session_confidence(s, cfg)
        s["decision"] = classify_session(s, cfg)
        s["recurrence_group"] = False
        s["ignition"] = s.get("ignition_status")
        s["latitude"] = s.get("lat")
        s["longitude"] = s.get("lon")
        rows.append(s)

    result = pd.DataFrame(rows)
    if not result.empty:
        result = result[result["decision"] != "IGNORED_NOISE"].reset_index(drop=True)
        mask = result["discarded_as_glitch"] == True  # noqa: E712
        result.loc[mask, "decision"] = "LIKELY_NOISE"
        result.loc[mask, "confidence"] = result.loc[mask, "confidence"].clip(upper=0.05)

        # Last gate: a day cannot drain more than the tank held plus what was added.
        if getattr(config, "MASS_BALANCE_ENABLED", False):
            result, mb = mass_balance.enforce(
                prepared, result, slack=getattr(config, "MASS_BALANCE_SLACK", 1.25)
            )
            if mb:
                print(
                    f"[mass_balance] {mb['drained_liters']}L drained vs {mb['budget_liters']}L "
                    f"budget ({mb['ratio']}x) -> demoted {mb['demoted_events']} events "
                    f"({mb['demoted_liters']}L)"
                )

    return prepared, result


def run_detection_v3(
    telemetry: pd.DataFrame,
    cfg: Optional[DetectorConfig] = None,
) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """Drop-in replacement for scoring.run_detection using session pipeline."""
    return run_session_pipeline(telemetry, cfg)
