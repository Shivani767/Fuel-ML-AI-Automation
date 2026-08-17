"""Per-device fuel-sensor sensitivity to supply voltage.

On most of this fleet the reported fuel level moves with the supply rail. Measured
across 56 devices on one day, 33 of them (59%) show at least 2 L of apparent fuel
change attributable to voltage alone, and several are far worse:

    Gawar HIM TIP        corr +0.61    89.5 L of swing
    Shiva                corr +0.69    50.6 L
    SK Genset DG-58Kva   corr +0.72    36.6 L
    Kamdhenu FBD TM      corr +1.00     5.9 L   (the reading *is* voltage)

The mechanism is visible in a raw trace: alternator charging at 13.8 V reads ~23 L,
the rail drops to 12.5 V and the same tank reads ~19 L, flipping minute to minute
while driving. Cranking makes it worse -- external voltage sags a median 0.66 V at
ignition start, and up to 12.9 V.

`swing_liters` is how many litres of phantom change that coupling can produce on a
given device. An alert smaller than that cannot be distinguished from the sensor
reacting to its own power supply, so the alert gate requires a real drop to clear a
multiple of it.

A large swing is also a hardware finding, not merely a filter input: a device whose
swing exceeds any plausible theft cannot report theft at all until it is recalibrated.
"""

from __future__ import annotations

import glob
import gzip
import json
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Dict, Optional

import numpy as np

from backend import config

PROFILE_PATH = config.PROJECT_ROOT / "fuel_data" / "voltage_profiles.json"
DAILY_DIR = config.PROJECT_ROOT / "fuel_data" / "daily"

# Below these a fit is meaningless: a flat tank or a flat rail tells us nothing.
MIN_SAMPLES = 300
MIN_FUEL_STD = 0.3
MIN_VOLT_STD = 0.05

# slope x voltage-range is only evidence of coupling if the correlation supports it.
# A weakly-correlated fit still produces a large slope from noise -- one device showed
# a 110 L "swing" at r=+0.31 -- and suppressing alerts on that basis would blind the
# device completely. Below this |r| we claim no coupling at all.
MIN_CORR_FOR_COUPLING = 0.40

_cache: Optional[Dict[str, dict]] = None


def _ensure_detector_path() -> None:
    root = str(config.PROJECT_ROOT / "fuel-theft-detector")
    if root not in sys.path:
        sys.path.insert(0, root)


def _read_day(imei: str, day: str) -> list:
    """Raw records for one IMEI-day from the capture store, gz or plain."""
    base = DAILY_DIR / day / imei
    for path, opener in ((base.with_suffix(".json.gz"), gzip.open),
                         (base.with_suffix(".json"), open)):
        if path.exists():
            try:
                with opener(path, "rt", encoding="utf-8") as f:
                    return json.load(f)
            except (OSError, ValueError):
                return []
    return []


def fit_device(imei: str, days: int = 7) -> Optional[dict]:
    """Fit fuel_level ~ external_voltage for one device over recent captured days."""
    _ensure_detector_path()
    from data.fetch import normalize_records  # noqa: E402

    recs: list = []
    today = date.today()
    for back in range(days + 1):
        recs += _read_day(imei, (today - timedelta(days=back)).isoformat())
        if len(recs) > 20000:
            break
    if len(recs) < MIN_SAMPLES:
        return None

    df = normalize_records(recs, imei=imei)
    if df.empty:
        return None

    fuel = df["fuel_level"].astype(float).to_numpy()
    volt = df["external_voltage"].astype(float).to_numpy()
    ok = ~(np.isnan(fuel) | np.isnan(volt))
    fuel, volt = fuel[ok], volt[ok]
    if len(fuel) < MIN_SAMPLES or fuel.std() < MIN_FUEL_STD or volt.std() < MIN_VOLT_STD:
        return None

    corr = float(np.corrcoef(fuel, volt)[0, 1])
    slope = float(np.polyfit(volt, fuel, 1)[0])
    v_range = float(np.percentile(volt, 95) - np.percentile(volt, 5))
    raw_swing = abs(slope * v_range)

    # Only credit coupling the correlation actually supports, and scale by |r| so a
    # partial relationship suppresses proportionally rather than all-or-nothing.
    coupled = abs(corr) >= MIN_CORR_FOR_COUPLING
    swing = raw_swing * abs(corr) if coupled else 0.0

    return {
        "imei": str(imei),
        "vehicle": str(df["vehicle_name_raw"].iloc[0]) if "vehicle_name_raw" in df else "",
        "corr": round(corr, 3),
        "slope_l_per_v": round(slope, 3),
        "voltage_range_v": round(v_range, 2),
        "raw_swing_liters": round(raw_swing, 2),
        "swing_liters": round(swing, 2),
        "coupled": bool(coupled),
        "samples": int(len(fuel)),
        # This much phantom swing swamps any realistic theft on this sensor.
        "needs_recalibration": bool(swing >= 10.0),
    }


def build_profiles(days: int = 7, imeis: Optional[list] = None) -> Dict[str, dict]:
    """Fit every device that has captured data and persist the result."""
    if imeis is None:
        imeis = sorted({
            Path(p).name.split(".")[0]
            for p in glob.glob(str(DAILY_DIR / "*" / "*.json.gz"))
        })

    out: Dict[str, dict] = {}
    for imei in imeis:
        try:
            prof = fit_device(imei, days=days)
        except Exception as e:  # a bad device must not stop the sweep
            print(f"[voltage] {imei}: fit failed: {e}")
            continue
        if prof:
            out[str(imei)] = prof

    PROFILE_PATH.parent.mkdir(parents=True, exist_ok=True)
    PROFILE_PATH.write_text(json.dumps(out, indent=1, sort_keys=True), encoding="utf-8")

    global _cache
    _cache = out
    return out


def load_profiles(refresh: bool = False) -> Dict[str, dict]:
    global _cache
    if _cache is not None and not refresh:
        return _cache
    if PROFILE_PATH.exists():
        try:
            _cache = json.loads(PROFILE_PATH.read_text())
            return _cache
        except (OSError, ValueError):
            pass
    _cache = {}
    return _cache


def swing_liters(imei: str) -> float:
    """Phantom litres this device's voltage coupling can produce. 0 if unknown.

    Unknown deliberately returns 0 rather than a guess: an unprofiled device should
    not have real alerts suppressed by a number nobody measured.
    """
    prof = load_profiles().get(str(imei))
    return float(prof.get("swing_liters", 0.0)) if prof else 0.0


def needs_recalibration() -> list:
    """Devices whose voltage coupling exceeds any plausible theft signal."""
    return sorted(
        (p for p in load_profiles().values() if p.get("needs_recalibration")),
        key=lambda p: -p.get("swing_liters", 0),
    )
