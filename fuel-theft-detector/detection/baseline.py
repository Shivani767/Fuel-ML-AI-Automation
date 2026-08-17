"""Expected-consumption model (idle / running / distance)."""

from __future__ import annotations

from typing import Optional

import numpy as np
import pandas as pd

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import DetectorConfig, DEFAULT_CONFIG, VehicleTypeBurnRates


def expected_change_row(
    ignition: str,
    engine_hours_delta: float,
    distance_km: float,
    movement: bool,
    rates: VehicleTypeBurnRates,
) -> float:
    """Expected fuel delta (negative when burning). Ignition off → 0."""
    if str(ignition).lower() in ("off", "0", "false"):
        return 0.0

    eh = max(float(engine_hours_delta or 0.0), 0.0)
    idle_burn = eh * rates.idle_burn_rate_lph
    running_burn = eh * rates.running_burn_rate_lph * rates.load_factor

    distance_burn = 0.0
    # GPS distance while stationary is drift — only count when movement confirmed
    if bool(movement) and rates.fuel_efficiency_kmpl and rates.fuel_efficiency_kmpl > 0:
        distance_burn = max(float(distance_km or 0.0), 0.0) / rates.fuel_efficiency_kmpl

    burn = max(idle_burn, running_burn, distance_burn)
    return -burn


def attach_expected_consumption(
    df: pd.DataFrame,
    cfg: Optional[DetectorConfig] = None,
    vehicle_type_col: str = "vehicle_type",
) -> pd.DataFrame:
    """
    Add:
      expected_change, actual_change, residual
    Requires fuel_used (from smoothing) or fuel_smooth / fuel_level.
    """
    cfg = cfg or DEFAULT_CONFIG
    out = df.copy()
    if out.empty:
        for col in ("expected_change", "actual_change", "residual", "truly_parked"):
            out[col] = []
        return out

    out = out.sort_values("timestamp").reset_index(drop=True)
    fuel_col = "fuel_used" if "fuel_used" in out.columns else (
        "fuel_smooth" if "fuel_smooth" in out.columns else "fuel_level"
    )
    fuel = pd.to_numeric(out[fuel_col], errors="coerce")
    out["actual_change"] = fuel.diff().fillna(0.0)

    expected = []
    for _, row in out.iterrows():
        vtype = row.get(vehicle_type_col) or "default"
        rates = cfg.rates_for(str(vtype))
        expected.append(
            expected_change_row(
                ignition=row.get("ignition", "off"),
                engine_hours_delta=row.get("engine_hours_delta", 0.0) or 0.0,
                distance_km=row.get("distance_km", 0.0) or 0.0,
                movement=bool(row.get("movement", False)),
                rates=rates,
            )
        )
    out["expected_change"] = expected
    out["residual"] = out["actual_change"] - out["expected_change"]

    # Stationary gate: speed==0 AND movement==false
    speed = pd.to_numeric(out.get("speed_kmh"), errors="coerce").fillna(0.0)
    movement = out.get("movement")
    if movement is None:
        movement = pd.Series([False] * len(out))
    out["truly_parked"] = (speed <= 0.0) & (~movement.astype(bool)) & (
        out["ignition"].astype(str).str.lower().isin(["off", "0", "false"])
    )
    return out
