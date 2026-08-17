"""Voltage / power-based tamper signals."""

from __future__ import annotations

from typing import List, Optional, Tuple

import numpy as np
import pandas as pd

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import DetectorConfig, DEFAULT_CONFIG


def attach_voltage_flags(
    df: pd.DataFrame,
    cfg: Optional[DetectorConfig] = None,
) -> pd.DataFrame:
    """Flag sharp external-voltage drops (relative % + absolute) and low internal voltage.

    Relative drop handles mixed 12V/24V fleets: a % drop from the vehicle's own
    recent nominal voltage, not a single flat volt threshold alone.
    """
    cfg = cfg or DEFAULT_CONFIG
    out = df.copy()
    if out.empty:
        out["voltage_drop"] = []
        out["low_internal_voltage"] = []
        out["tamper_signal"] = []
        return out

    out = out.sort_values("timestamp").reset_index(drop=True)
    ext = pd.to_numeric(out.get("external_voltage"), errors="coerce")
    internal = pd.to_numeric(out.get("internal_voltage"), errors="coerce")

    drop = ext.diff()
    # Rolling nominal (recent median) for relative % threshold
    nominal = ext.rolling(window=20, min_periods=3).median()
    pct_drop = (-drop) / nominal.replace(0, np.nan)
    relative_hit = pct_drop.fillna(0.0) >= 0.15  # ≥15% sudden drop
    absolute_hit = drop.fillna(0.0) <= -abs(cfg.voltage_drop_tamper)

    out["voltage_drop"] = relative_hit | absolute_hit
    out["low_internal_voltage"] = internal.notna() & (
        internal < cfg.low_internal_voltage
    )
    out["tamper_signal"] = out["voltage_drop"]
    return out


def tamper_near(
    df: pd.DataFrame,
    event_time: pd.Timestamp,
    cfg: Optional[DetectorConfig] = None,
) -> Tuple[bool, Optional[pd.Timestamp]]:
    """True if a voltage-drop tamper occurred within lookback before event_time."""
    cfg = cfg or DEFAULT_CONFIG
    if df.empty or "tamper_signal" not in df.columns:
        return False, None
    start = event_time - pd.Timedelta(minutes=cfg.tamper_lookback_minutes)
    window = df[(df["timestamp"] >= start) & (df["timestamp"] <= event_time)]
    hits = window[window["tamper_signal"] == True]  # noqa: E712
    if hits.empty:
        return False, None
    return True, hits.iloc[-1]["timestamp"]


def list_tamper_events(df: pd.DataFrame) -> List[dict]:
    if df.empty or "tamper_signal" not in df.columns:
        return []
    events = []
    for _, row in df[df["tamper_signal"] == True].iterrows():  # noqa: E712
        events.append(
            {
                "timestamp": row["timestamp"],
                "external_voltage": row.get("external_voltage"),
                "internal_voltage": row.get("internal_voltage"),
            }
        )
    return events
