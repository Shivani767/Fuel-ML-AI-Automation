"""Thresholds, burn rates, and API settings. Secrets come from environment variables."""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict

# Repo root holds .env and the loader; this package is one level below it.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
try:
    from env import load_env

    load_env()
except ImportError:  # standalone use outside the repo
    pass


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    return float(raw)


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    return int(raw)


# --- API ---
FUELARK_API_URL = os.environ.get(
    "FUELARK_API_URL",
    "https://api1.fuelark.com/api/FuelConsumption/DeviceFuelConsumption",
)
FUELARK_API_KEY = os.environ.get("FUELARK_API_KEY", "")
TIMEZONE = os.environ.get("FUEL_DETECTOR_TZ", "Asia/Kolkata")

# --- Cache / IO ---
PACKAGE_ROOT = os.path.dirname(os.path.abspath(__file__))
CACHE_DIR = os.environ.get(
    "FUEL_DETECTOR_CACHE", os.path.join(PACKAGE_ROOT, "data", "cache")
)
OUTPUT_DIR = os.environ.get(
    "FUEL_DETECTOR_OUTPUT", os.path.join(PACKAGE_ROOT, "output")
)
ASSIGNMENTS_CSV = os.environ.get(
    "FUEL_DETECTOR_ASSIGNMENTS",
    os.path.join(PACKAGE_ROOT, "data", "vehicle_assignments.csv"),
)

# --- Detection magnitude bands (liters) ---
IGNORE_BELOW_LITERS = _env_float("IGNORE_BELOW_LITERS", 2.0)
CONFIRMED_ABOVE_LITERS = _env_float("CONFIRMED_ABOVE_LITERS", 5.0)
AMBIGUOUS_BAND_LOW = IGNORE_BELOW_LITERS
AMBIGUOUS_BAND_HIGH = CONFIRMED_ABOVE_LITERS

# --- Shape / recurrence / sessions (v3) ---
RECURRENCE_WINDOW_MINUTES = _env_int("RECURRENCE_WINDOW_MINUTES", 120)
RECURRENCE_LOCATION_RADIUS_METERS = _env_float("RECURRENCE_LOCATION_RADIUS_METERS", 50.0)
BOUNCE_BACK_TOLERANCE_LITERS = _env_float("BOUNCE_BACK_TOLERANCE_LITERS", 1.0)
BOUNCE_BACK_LOOKAHEAD_SAMPLES = _env_int("BOUNCE_BACK_LOOKAHEAD_SAMPLES", 5)
PLATEAU_STD_LITERS = _env_float("PLATEAU_STD_LITERS", 1.5)
PLATEAU_LOOKAHEAD_SAMPLES = _env_int("PLATEAU_LOOKAHEAD_SAMPLES", 6)
PLATEAU_CHECK_READINGS = _env_int("PLATEAU_CHECK_READINGS", 3)
MAX_EVENT_DURATION_MINUTES = _env_float("MAX_EVENT_DURATION_MINUTES", 20.0)
SESSION_MAX_GAP_MINUTES = _env_float("SESSION_MAX_GAP_MINUTES", 25.0)

# --- Dual channel ---
FUEL_CHANNEL_DIVERGENCE_LITERS = _env_float("FUEL_CHANNEL_DIVERGENCE_LITERS", 4.0)
SMOOTH_WINDOW = _env_int("SMOOTH_WINDOW", 3)

# --- Tamper / voltage (FuelArk iO_66/iO_67 millivolts → volts in fetch.py) ---
# v3: sudden external drop ≥ 8 V is a strong disconnect / tamper precursor.
VOLTAGE_DROP_TAMPER_THRESHOLD = _env_float("VOLTAGE_DROP_TAMPER_THRESHOLD", 8.0)
TAMPER_LOOKBACK_MINUTES = _env_float("TAMPER_LOOKBACK_MINUTES", 30.0)
LOW_INTERNAL_VOLTAGE_THRESHOLD = _env_float("LOW_INTERNAL_VOLTAGE_THRESHOLD", 3.5)

# Default burn rates (v3 baselines; override per vehicle_type in DEFAULT_BURN_RATES)
IDLE_BURN_RATE_LPH = _env_float("IDLE_BURN_RATE_LPH", 0.8)
RUNNING_BURN_RATE_LPH = _env_float("RUNNING_BURN_RATE_LPH", 3.5)
FUEL_EFFICIENCY_KMPL = _env_float("FUEL_EFFICIENCY_KMPL", 4.0)

# --- Confidence tiers ---
AUTO_CONFIRMED_MIN = _env_float("AUTO_CONFIRMED_MIN", 0.7)

# Per-vehicle-day physical plausibility: total drains cannot exceed the observed
# fuel span plus everything filled. Measured 2.96x / 2.63x / 1.56x overshoot on
# three of ten ground-truth vehicle-days. Slack absorbs sensor noise; only clear
# violations are acted on.
MASS_BALANCE_ENABLED = _env_int("MASS_BALANCE_ENABLED", 1) == 1
MASS_BALANCE_SLACK = _env_float("MASS_BALANCE_SLACK", 1.25)

# Sessions close on any direction change, so a momentary counter-tick splits one
# event into fragments -- a confirmed 204 L theft came out as 87.58 + 117.33 with a
# 0.5 L blip and 30 s between them. Rejoin only when the interruption is small in
# both time and volume, so distinct events are never welded together.
SPLIT_MERGE_MAX_GAP_MINUTES = _env_float("SPLIT_MERGE_MAX_GAP_MINUTES", 3.0)
SPLIT_MERGE_MAX_REBOUND_LITERS = _env_float("SPLIT_MERGE_MAX_REBOUND_LITERS", 2.0)
SPLIT_MERGE_REBOUND_FRACTION = _env_float("SPLIT_MERGE_REBOUND_FRACTION", 0.05)
MANUAL_REVIEW_MIN = _env_float("MANUAL_REVIEW_MIN", 0.4)

# --- API client behaviour ---
API_MAX_RETRIES = _env_int("API_MAX_RETRIES", 3)
API_RETRY_BASE_SECONDS = _env_float("API_RETRY_BASE_SECONDS", 1.5)
API_REQUEST_GAP_SECONDS = _env_float("API_REQUEST_GAP_SECONDS", 0.2)


@dataclass(frozen=True)
class VehicleTypeBurnRates:
    idle_burn_rate_lph: float = IDLE_BURN_RATE_LPH
    running_burn_rate_lph: float = RUNNING_BURN_RATE_LPH
    fuel_efficiency_kmpl: float = FUEL_EFFICIENCY_KMPL
    load_factor: float = 1.0


DEFAULT_BURN_RATES: Dict[str, VehicleTypeBurnRates] = {
    "truck": VehicleTypeBurnRates(idle_burn_rate_lph=0.8, running_burn_rate_lph=3.5, fuel_efficiency_kmpl=4.0),
    "tractor": VehicleTypeBurnRates(idle_burn_rate_lph=1.2, running_burn_rate_lph=5.0, fuel_efficiency_kmpl=2.5),
    "tipper": VehicleTypeBurnRates(idle_burn_rate_lph=1.0, running_burn_rate_lph=6.0, fuel_efficiency_kmpl=2.2),
    "car": VehicleTypeBurnRates(idle_burn_rate_lph=0.5, running_burn_rate_lph=2.5, fuel_efficiency_kmpl=12.0),
    "dg": VehicleTypeBurnRates(idle_burn_rate_lph=2.0, running_burn_rate_lph=5.0, fuel_efficiency_kmpl=0.0, load_factor=1.0),
    "default": VehicleTypeBurnRates(),
}


@dataclass
class DetectorConfig:
    ignore_below: float = IGNORE_BELOW_LITERS
    confirmed_above: float = CONFIRMED_ABOVE_LITERS
    recurrence_window_minutes: int = RECURRENCE_WINDOW_MINUTES
    recurrence_location_radius_m: float = RECURRENCE_LOCATION_RADIUS_METERS
    bounce_back_tolerance: float = BOUNCE_BACK_TOLERANCE_LITERS
    bounce_back_lookahead: int = BOUNCE_BACK_LOOKAHEAD_SAMPLES
    plateau_std: float = PLATEAU_STD_LITERS
    plateau_lookahead: int = PLATEAU_LOOKAHEAD_SAMPLES
    plateau_check_readings: int = PLATEAU_CHECK_READINGS
    max_event_duration_minutes: float = MAX_EVENT_DURATION_MINUTES
    session_max_gap_minutes: float = SESSION_MAX_GAP_MINUTES
    channel_divergence: float = FUEL_CHANNEL_DIVERGENCE_LITERS
    smooth_window: int = SMOOTH_WINDOW
    voltage_drop_tamper: float = VOLTAGE_DROP_TAMPER_THRESHOLD
    tamper_lookback_minutes: float = TAMPER_LOOKBACK_MINUTES
    low_internal_voltage: float = LOW_INTERNAL_VOLTAGE_THRESHOLD
    auto_confirmed_min: float = AUTO_CONFIRMED_MIN
    manual_review_min: float = MANUAL_REVIEW_MIN
    # Rejoining fragments a momentary counter-tick split apart.
    split_merge_max_gap_minutes: float = SPLIT_MERGE_MAX_GAP_MINUTES
    split_merge_max_rebound_liters: float = SPLIT_MERGE_MAX_REBOUND_LITERS
    split_merge_rebound_fraction: float = SPLIT_MERGE_REBOUND_FRACTION
    burn_rates: Dict[str, VehicleTypeBurnRates] = field(
        default_factory=lambda: dict(DEFAULT_BURN_RATES)
    )

    def rates_for(self, vehicle_type: str = "default") -> VehicleTypeBurnRates:
        key = (vehicle_type or "default").lower()
        return self.burn_rates.get(key, self.burn_rates["default"])


DEFAULT_CONFIG = DetectorConfig()
