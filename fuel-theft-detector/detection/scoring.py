"""Confidence scoring and routing: AUTO_CONFIRMED / MANUAL_REVIEW / LIKELY_NOISE."""

from __future__ import annotations

from typing import Dict, List, Optional

import pandas as pd

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import DetectorConfig, DEFAULT_CONFIG
from detection.events import CandidateEvent


DECISION_AUTO = "AUTO_CONFIRMED"
DECISION_REVIEW = "MANUAL_REVIEW"
DECISION_NOISE = "LIKELY_NOISE"


def score_event(event: CandidateEvent, cfg: Optional[DetectorConfig] = None) -> Dict:
    """
    Weighted multi-signal score in [0, 1].
    Never binary-classify from magnitude alone.
    """
    cfg = cfg or DEFAULT_CONFIG

    if event.discarded_as_glitch:
        return {
            **event.to_dict(),
            "confidence": 0.05,
            "decision": DECISION_NOISE,
            "score_breakdown": {"discarded": event.discard_reason or "glitch"},
        }

    breakdown = {}
    score = 0.0

    # Magnitude
    q = abs(event.quantity_liters)
    if q >= cfg.confirmed_above:
        mag = 0.35
    elif q >= cfg.ignore_below:
        mag = 0.15 + 0.20 * ((q - cfg.ignore_below) / max(cfg.confirmed_above - cfg.ignore_below, 1e-6))
    else:
        mag = 0.0
    breakdown["magnitude"] = round(mag, 3)
    score += mag

    # Bounce-back is negative evidence (already discarded usually)
    if event.bounce_back:
        breakdown["bounce_back"] = -0.35
        score -= 0.35
    else:
        breakdown["bounce_back"] = 0.10
        score += 0.10

    # Plateau stability
    if event.plateau_ok:
        breakdown["plateau"] = 0.15
        score += 0.15
    else:
        breakdown["plateau"] = -0.05
        score -= 0.05

    # Ignition / stationary match
    if event.event_type == "DRAIN":
        if event.truly_parked or str(event.ignition).lower() == "off":
            breakdown["stationary_match"] = 0.20
            score += 0.20
        else:
            breakdown["stationary_match"] = -0.10
            score -= 0.10
    else:  # FILL — often ignition off while parked at pump, or on
        if event.duration_ok:
            breakdown["stationary_match"] = 0.10
            score += 0.10
        else:
            breakdown["stationary_match"] = 0.0

    # Tamper is one of the strongest theft signals
    if event.tamper_flag and event.event_type == "DRAIN":
        breakdown["tamper"] = 0.25
        score += 0.25
    else:
        breakdown["tamper"] = 0.0

    # Recurrence escalation
    if event.recurrence_group:
        breakdown["recurrence"] = 0.15
        score += 0.15
    else:
        breakdown["recurrence"] = 0.0

    # Penalties
    if event.channel_disagreement:
        breakdown["channel_disagreement"] = -0.12
        score -= 0.12
    else:
        breakdown["channel_disagreement"] = 0.0

    if event.low_internal_voltage:
        breakdown["low_internal_voltage"] = -0.08
        score -= 0.08
    else:
        breakdown["low_internal_voltage"] = 0.0

    if not event.duration_ok:
        breakdown["duration_penalty"] = -0.10
        score -= 0.10
    else:
        breakdown["duration_penalty"] = 0.0

    # Ambiguous band without strong extras → keep mid
    if cfg.ignore_below <= q < cfg.confirmed_above and not event.recurrence_group:
        if not event.plateau_ok:
            breakdown["ambiguous_unstable"] = -0.05
            score -= 0.05

    confidence = max(0.0, min(1.0, score))
    if confidence >= cfg.auto_confirmed_min:
        decision = DECISION_AUTO
    elif confidence >= cfg.manual_review_min:
        decision = DECISION_REVIEW
    else:
        decision = DECISION_NOISE

    # Unstable fill → never auto-confirm
    if event.event_type == "FILL" and not event.plateau_ok and decision == DECISION_AUTO:
        decision = DECISION_REVIEW
        confidence = min(confidence, cfg.auto_confirmed_min - 0.01)

    return {
        **event.to_dict(),
        "confidence": round(confidence, 3),
        "decision": decision,
        "score_breakdown": breakdown,
    }


def score_candidates(
    candidates: List[CandidateEvent],
    cfg: Optional[DetectorConfig] = None,
) -> pd.DataFrame:
    rows = [score_event(c, cfg) for c in candidates]
    if not rows:
        return pd.DataFrame(
            columns=[
                "imei",
                "timestamp",
                "event_type",
                "quantity_liters",
                "confidence",
                "decision",
                "tamper_flag",
                "vehicle_name",
            ]
        )
    return pd.DataFrame(rows)


def run_detection(
    telemetry: pd.DataFrame,
    cfg: Optional[DetectorConfig] = None,
    *,
    use_sessions: bool = True,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """
    End-to-end detection on a canonical telemetry frame.
    Returns (prepared_frame, scored_events_df).

    Default (v3): session consolidation first, then score each session once.
    Pass use_sessions=False for legacy per-tick residual candidates.
    """
    cfg = cfg or DEFAULT_CONFIG
    if use_sessions:
        from detection.pipeline_v3 import run_session_pipeline

        return run_session_pipeline(telemetry, cfg)

    from detection.events import prepare_frame, detect_candidates

    prepared = prepare_frame(telemetry, cfg)
    candidates = detect_candidates(prepared, cfg)
    events = score_candidates(candidates, cfg)
    return prepared, events
