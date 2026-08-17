"""Candidate detection, shape validation, and recurrence escalation."""

from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import List, Optional

import numpy as np
import pandas as pd

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import DetectorConfig, DEFAULT_CONFIG
from detection.tamper import tamper_near


@dataclass
class CandidateEvent:
    imei: str
    timestamp: object
    event_type: str  # FILL | DRAIN
    quantity_liters: float
    residual: float
    start_idx: int
    end_idx: int
    duration_minutes: float
    bounce_back: bool
    plateau_ok: bool
    duration_ok: bool
    truly_parked: bool
    ignition: str
    channel_disagreement: bool
    low_internal_voltage: bool
    tamper_flag: bool
    tamper_time: object
    recurrence_group: bool
    soft_alert: object
    vehicle_name: object
    vehicle_type: str
    latitude: object
    longitude: object
    discarded_as_glitch: bool = False
    discard_reason: Optional[str] = None

    def to_dict(self) -> dict:
        d = asdict(self)
        for key in ("timestamp", "tamper_time"):
            val = d.get(key)
            if hasattr(val, "isoformat"):
                d[key] = val.isoformat()
        return d


def _bounce_back(fuel: np.ndarray, idx: int, direction: str, cfg: DetectorConfig) -> bool:
    """True if level recovers close to pre-event value within a few readings."""
    if idx <= 0:
        return False
    pre = float(fuel[idx - 1])
    end = min(len(fuel), idx + 1 + cfg.bounce_back_lookahead)
    post = fuel[idx + 1 : end]
    if len(post) == 0:
        return False
    # Recovered if any post sample is within tolerance of pre level
    return bool(np.nanmin(np.abs(post - pre)) <= cfg.bounce_back_tolerance)


def _plateau_ok(fuel: np.ndarray, idx: int, cfg: DetectorConfig) -> bool:
    end = min(len(fuel), idx + 1 + cfg.plateau_lookahead)
    post = fuel[idx + 1 : end]
    # Not enough post-event samples to prove stability → treat as unstable
    # (keeps AUTO_CONFIRMED honest; scoring can still MANUAL_REVIEW)
    if len(post) < max(3, cfg.plateau_lookahead // 2):
        return False
    std_ok = float(np.nanstd(post)) <= cfg.plateau_std
    span_ok = float(np.nanmax(post) - np.nanmin(post)) <= cfg.plateau_std * 2.5
    return std_ok and span_ok


def _duration_minutes(timestamps: pd.Series, start_idx: int, end_idx: int) -> float:
    t0 = timestamps.iloc[start_idx]
    t1 = timestamps.iloc[end_idx]
    return max((t1 - t0).total_seconds() / 60.0, 0.0)


def detect_candidates(
    df: pd.DataFrame,
    cfg: Optional[DetectorConfig] = None,
) -> List[CandidateEvent]:
    """
    Scan residual series for FILL / DRAIN candidates and apply shape checks.
    Expects columns from smoothing + baseline + tamper.
    """
    cfg = cfg or DEFAULT_CONFIG
    if df is None or df.empty or "residual" not in df.columns:
        return []

    work = df.sort_values("timestamp").reset_index(drop=True)
    # Residuals use smoothed channel; shape checks prefer raw level when available
    fuel_smooth = pd.to_numeric(
        work.get("fuel_used", work.get("fuel_smooth", work["fuel_level"])),
        errors="coerce",
    ).to_numpy(dtype=float)
    fuel_raw = pd.to_numeric(work["fuel_level"], errors="coerce").to_numpy(dtype=float)
    fuel = np.where(np.isnan(fuel_raw), fuel_smooth, fuel_raw)
    residual = pd.to_numeric(work["residual"], errors="coerce").fillna(0.0).to_numpy()

    candidates: List[CandidateEvent] = []

    i = 1
    n = len(work)
    while i < n:
        res = float(residual[i])
        mag = abs(res)
        if mag < cfg.ignore_below:
            i += 1
            continue

        # Expand short contiguous run in the same direction
        direction = "FILL" if res > 0 else "DRAIN"
        j = i
        total_res = res
        while j + 1 < n:
            nxt = float(residual[j + 1])
            if abs(nxt) < cfg.ignore_below * 0.5:
                break
            if (direction == "FILL" and nxt < 0) or (direction == "DRAIN" and nxt > 0):
                break
            total_res += nxt
            j += 1

        quantity = abs(float(total_res))
        if quantity < cfg.ignore_below:
            i = j + 1
            continue

        duration = _duration_minutes(work["timestamp"], i, j)
        duration_ok = duration <= cfg.max_event_duration_minutes
        bounce = _bounce_back(fuel, j, direction, cfg)
        plateau = _plateau_ok(fuel, j, cfg)

        row = work.iloc[j]
        discarded = False
        discard_reason = None
        if bounce:
            discarded = True
            discard_reason = "bounce_back_glitch"
        elif not duration_ok and quantity < cfg.confirmed_above:
            # Slow drift in ambiguous band — ignore
            discarded = True
            discard_reason = "duration_too_long"

        has_tamper, tamper_ts = tamper_near(work, row["timestamp"], cfg)

        event = CandidateEvent(
            imei=str(row.get("imei")),
            timestamp=row["timestamp"],
            event_type=direction,
            quantity_liters=round(quantity, 3),
            residual=round(float(total_res), 3),
            start_idx=int(i),
            end_idx=int(j),
            duration_minutes=round(duration, 3),
            bounce_back=bool(bounce),
            plateau_ok=bool(plateau),
            duration_ok=bool(duration_ok),
            truly_parked=bool(row.get("truly_parked", False)),
            ignition=str(row.get("ignition", "off")),
            channel_disagreement=bool(row.get("channel_disagreement", False)),
            low_internal_voltage=bool(row.get("low_internal_voltage", False)),
            tamper_flag=bool(has_tamper),
            tamper_time=tamper_ts,
            recurrence_group=False,
            soft_alert=row.get("soft_alert"),
            vehicle_name=row.get("vehicle_name"),
            vehicle_type=str(row.get("vehicle_type") or "default"),
            latitude=row.get("latitude"),
            longitude=row.get("longitude"),
            discarded_as_glitch=discarded,
            discard_reason=discard_reason,
        )
        candidates.append(event)
        i = j + 1

    candidates = _apply_recurrence(candidates, work, cfg)
    return candidates


def _apply_recurrence(
    candidates: List[CandidateEvent],
    df: pd.DataFrame,
    cfg: DetectorConfig,
) -> List[CandidateEvent]:
    """
    Group small (2–5L) drains in a rolling parked window; escalate if sum > 5L.
    """
    drains = [
        c
        for c in candidates
        if c.event_type == "DRAIN"
        and not c.discarded_as_glitch
        and cfg.ignore_below <= c.quantity_liters < cfg.confirmed_above
    ]
    if not drains:
        return candidates

    used = set()
    escalated: List[CandidateEvent] = []
    for idx, c in enumerate(drains):
        if idx in used:
            continue
        window_end = pd.Timestamp(c.timestamp) + pd.Timedelta(
            minutes=cfg.recurrence_window_minutes
        )
        group = [c]
        group_idx = [idx]
        for jdx in range(idx + 1, len(drains)):
            other = drains[jdx]
            if jdx in used:
                continue
            if other.imei != c.imei:
                continue
            if pd.Timestamp(other.timestamp) > window_end:
                break
            # Same parked session preference
            if c.truly_parked and not other.truly_parked:
                continue
            group.append(other)
            group_idx.append(jdx)

        total = sum(g.quantity_liters for g in group)
        if total >= cfg.confirmed_above and len(group) >= 2:
            for g in group:
                g.recurrence_group = True
            # Represent escalated group as a single synthetic candidate
            last = group[-1]
            escalated.append(
                CandidateEvent(
                    imei=last.imei,
                    timestamp=last.timestamp,
                    event_type="DRAIN",
                    quantity_liters=round(total, 3),
                    residual=-round(total, 3),
                    start_idx=group[0].start_idx,
                    end_idx=last.end_idx,
                    duration_minutes=round(
                        (
                            pd.Timestamp(last.timestamp) - pd.Timestamp(group[0].timestamp)
                        ).total_seconds()
                        / 60.0,
                        3,
                    ),
                    bounce_back=False,
                    plateau_ok=all(g.plateau_ok for g in group),
                    duration_ok=True,
                    truly_parked=all(g.truly_parked for g in group),
                    ignition=last.ignition,
                    channel_disagreement=any(g.channel_disagreement for g in group),
                    low_internal_voltage=any(g.low_internal_voltage for g in group),
                    tamper_flag=any(g.tamper_flag for g in group),
                    tamper_time=next((g.tamper_time for g in group if g.tamper_time), None),
                    recurrence_group=True,
                    soft_alert=last.soft_alert,
                    vehicle_name=last.vehicle_name,
                    vehicle_type=last.vehicle_type,
                    latitude=last.latitude,
                    longitude=last.longitude,
                )
            )
            used.update(group_idx)

    # Drop members that were folded into an escalated group; keep others
    keep = []
    folded_times = {e.timestamp for e in escalated}
    member_ids = set()
    for idx in used:
        member_ids.add(id(drains[idx]))

    for c in candidates:
        if id(c) in member_ids:
            continue
        keep.append(c)
    keep.extend(escalated)
    keep.sort(key=lambda e: pd.Timestamp(e.timestamp))
    return keep


def prepare_frame(
    df: pd.DataFrame,
    cfg: Optional[DetectorConfig] = None,
) -> pd.DataFrame:
    """Full preprocess: smooth → baseline → tamper flags."""
    from detection.smoothing import smooth_telemetry
    from detection.baseline import attach_expected_consumption
    from detection.tamper import attach_voltage_flags

    cfg = cfg or DEFAULT_CONFIG
    out = smooth_telemetry(df, cfg)
    out = attach_expected_consumption(out, cfg)
    out = attach_voltage_flags(out, cfg)
    return out
