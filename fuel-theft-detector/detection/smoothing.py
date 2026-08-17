"""Rolling median smoothing and dual fuel-channel cross-validation."""

from __future__ import annotations

from typing import Optional

import numpy as np
import pandas as pd

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
from config import DetectorConfig, DEFAULT_CONFIG


def _scale_edc_to_liters(fuel: pd.Series, edc: pd.Series) -> pd.Series:
    """Map EDC raw units toward liter scale using a robust ratio when possible."""
    valid = fuel.notna() & edc.notna() & (edc.abs() > 1e-6)
    if valid.sum() < 5:
        return edc.astype(float)
    ratio = (fuel[valid] / edc[valid]).replace([np.inf, -np.inf], np.nan).median()
    if pd.isna(ratio) or ratio <= 0:
        return edc.astype(float)
    # If ratio ~1, EDC already looks like liters; if tiny, EDC is ADC counts
    return edc.astype(float) * float(ratio)


def smooth_telemetry(
    df: pd.DataFrame,
    cfg: Optional[DetectorConfig] = None,
) -> pd.DataFrame:
    """
    Add:
      fuel_smooth, edc_smooth, fuel_used (channel chosen after cross-check),
      channel_disagreement, short_window_var_fuel/edc
    """
    cfg = cfg or DEFAULT_CONFIG
    out = df.copy()
    if out.empty:
        for col in (
            "fuel_smooth",
            "edc_smooth",
            "edc_liters",
            "fuel_used",
            "channel_disagreement",
            "short_var_fuel",
            "short_var_edc",
        ):
            out[col] = []
        return out

    out = out.sort_values("timestamp").reset_index(drop=True)
    w = max(1, int(cfg.smooth_window))

    fuel = pd.to_numeric(out["fuel_level"], errors="coerce")
    edc_raw = pd.to_numeric(out.get("fuel_level_edc"), errors="coerce")
    edc_liters = _scale_edc_to_liters(fuel, edc_raw)

    out["fuel_smooth"] = fuel.rolling(window=w, min_periods=1, center=True).median()
    out["edc_liters"] = edc_liters
    out["edc_smooth"] = edc_liters.rolling(window=w, min_periods=1, center=True).median()

    out["short_var_fuel"] = (
        out["fuel_smooth"].rolling(window=max(w, 5), min_periods=2).std().fillna(0.0)
    )
    out["short_var_edc"] = (
        out["edc_smooth"].rolling(window=max(w, 5), min_periods=2).std().fillna(0.0)
    )

    divergence = (out["fuel_smooth"] - out["edc_smooth"]).abs()
    # Only flag disagreement when EDC is present and scaled plausibly
    edc_present = edc_raw.notna()
    out["channel_disagreement"] = edc_present & (divergence > cfg.channel_divergence)

    prefer_edc = out["channel_disagreement"] & (
        out["short_var_edc"] < out["short_var_fuel"]
    )
    out["fuel_used"] = out["fuel_smooth"].where(~prefer_edc, out["edc_smooth"])
    # v3 alias
    out["fuel_primary"] = out["fuel_used"]
    out["fuel_edc_smooth"] = out["edc_smooth"]
    out["channel_divergence"] = divergence
    return out
