"""Fuelark-style device metrics (summary cards)."""

from __future__ import annotations

from datetime import datetime
from math import asin, cos, radians, sin, sqrt
from typing import Any, Dict, List, Optional, Tuple


def _safe_float(value, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _haversine_km(lat1, lon1, lat2, lon2) -> float:
    try:
        lat1, lon1, lat2, lon2 = map(float, [lat1, lon1, lat2, lon2])
    except (TypeError, ValueError):
        return 0.0
    if abs(lat1) < 0.01 and abs(lon1) < 0.01:
        return 0.0
    if abs(lat2) < 0.01 and abs(lon2) < 0.01:
        return 0.0
    rlat1, rlon1, rlat2, rlon2 = map(radians, [lat1, lon1, lat2, lon2])
    dlat = rlat2 - rlat1
    dlon = rlon2 - rlon1
    a = sin(dlat / 2) ** 2 + cos(rlat1) * cos(rlat2) * sin(dlon / 2) ** 2
    dist = 2 * 6371.0 * asin(sqrt(min(a, 1.0)))
    return dist if dist < 50 else 0.0  # drop GPS jumps


def haversine_m(lat1, lon1, lat2, lon2) -> Optional[float]:
    """Metres between two fixes, or **None** when either is unusable.

    Deliberately not `_haversine_km` * 1000. That one answers 0.0 for a missing or
    null-island fix, which is right when accumulating a distance total but dangerous
    for "are these the same place?" -- 0.0 reads as *identical location*, and callers
    deciding whether to cancel an event on that basis would cancel it blind. None
    forces the caller to handle "unknown" as its own case.
    """
    try:
        lat1, lon1, lat2, lon2 = map(float, [lat1, lon1, lat2, lon2])
    except (TypeError, ValueError):
        return None
    if (abs(lat1) < 0.01 and abs(lon1) < 0.01) or (abs(lat2) < 0.01 and abs(lon2) < 0.01):
        return None
    rlat1, rlon1, rlat2, rlon2 = map(radians, [lat1, lon1, lat2, lon2])
    dlat, dlon = rlat2 - rlat1, rlon2 - rlon1
    a = sin(dlat / 2) ** 2 + cos(rlat1) * cos(rlat2) * sin(dlon / 2) ** 2
    return 2 * 6371000.0 * asin(sqrt(min(a, 1.0)))


def format_running_time(seconds: float) -> str:
    seconds = max(0, int(seconds))
    days, rem = divmod(seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, secs = divmod(rem, 60)
    return f"{days:02d}D: {hours:02d}H: {minutes:02d}M: {secs:02d}S"


def format_parking_since(seconds: float) -> str:
    """Fuelark-style continuous park duration (not total parked time in range)."""
    seconds = max(0, int(seconds))
    hours, rem = divmod(seconds, 3600)
    minutes, secs = divmod(rem, 60)
    if hours >= 100:
        days, hours = divmod(hours, 24)
        return f"Parking since {days}D {hours:02d}:{minutes:02d}:{secs:02d}"
    return f"Parking since {hours:02d}:{minutes:02d}:{secs:02d}"


def continuous_parking_seconds(data: List[dict]) -> float:
    """Seconds the vehicle has been parked ending at the last point in the window."""
    if not data:
        return 0.0
    last = data[-1]
    if int(last.get("iO_1", 0) or 0) == 1 or _safe_float(last.get("speed")) > 1.0:
        return 0.0
    try:
        end = datetime.fromisoformat(str(last["time"]))
    except Exception:
        return 0.0

    park_start = end
    for i in range(len(data) - 1, -1, -1):
        entry = data[i]
        ign = int(entry.get("iO_1", 0) or 0)
        speed = _safe_float(entry.get("speed"))
        if ign == 1 or speed > 1.0:
            break
        try:
            park_start = datetime.fromisoformat(str(entry["time"]))
        except Exception:
            break
    return max(0.0, (end - park_start).total_seconds())


def estimate_consumption(series: List[dict]) -> float:
    """Consumption only (ignore fill/theft EDC heuristics — those inflate wildly)."""
    total_consumption = 0.0
    previous = None
    for entry in series:
        if previous is None:
            previous = entry
            continue
        prev_fuel = _safe_float(previous.get("fuelInLiter", 0))
        curr_fuel = _safe_float(entry.get("fuelInLiter", 0))
        prev_ignition = int(previous.get("iO_1", 0) or 0)
        curr_ignition = int(entry.get("iO_1", 0) or 0)
        drop = max(0.0, prev_fuel - curr_fuel)
        # Ignore huge single-tick drops (fills/thefts/glitches) for consumption
        if drop > 15.0:
            previous = entry
            continue
        if prev_ignition == 1 or curr_ignition == 1:
            total_consumption += drop
        previous = entry
    return round(total_consumption, 2)


def compute_device_summary(
    device_name: str,
    data: List[dict],
    events: Optional[List[dict]] = None,
    *,
    detected_fills: Optional[List[dict]] = None,
    detected_thefts: Optional[List[dict]] = None,
) -> Dict[str, Any]:
    """Build Fuelark Live Map summary card payload.

    Fill/theft totals come ONLY from session detection (or verified cases),
    never from noisy fuel_events.json or crude tick heuristics.
    """
    if not data:
        return {}

    latest = data[-1]
    total_consumption = estimate_consumption(data)

    total_distance = 0.0
    for i in range(1, len(data)):
        total_distance += _haversine_km(
            data[i - 1].get("latitude"),
            data[i - 1].get("longitude"),
            data[i].get("latitude"),
            data[i].get("longitude"),
        )

    trip_count = 0
    in_trip = False
    for entry in data:
        speed = _safe_float(entry.get("speed", 0))
        if speed > 1.0 and not in_trip:
            in_trip = True
        elif speed <= 1.0 and in_trip:
            in_trip = False
            trip_count += 1

    running_time = 0.0
    for i in range(1, len(data)):
        if int(data[i - 1].get("iO_1", 0) or 0) == 1:
            try:
                t1 = datetime.fromisoformat(data[i - 1]["time"])
                t2 = datetime.fromisoformat(data[i]["time"])
                delta = (t2 - t1).total_seconds()
                if 0 < delta < 3600:
                    running_time += delta
            except Exception:
                pass

    parking_streak = continuous_parking_seconds(data)

    running_hours = running_time / 3600.0 if running_time > 0 else 0.0
    # Guard divide-by-zero / negative (never emit -Infinity)
    if running_hours > 0 and total_consumption > 0:
        avg_consumption_lph = round(total_consumption / running_hours, 2)
    else:
        avg_consumption_lph = None
    if total_consumption > 0 and total_distance > 0:
        mileage = round(total_distance / total_consumption, 2)
    else:
        mileage = None

    # Prefer explicit detection results
    fills = detected_fills or []
    thefts = detected_thefts or []
    def _accepted_source(src: str) -> bool:
        s = str(src or "")
        return (
            s.startswith("detector_v3")
            or s in ("verified_theft_events", "heuristic", "fuel_events")
        )

    if not fills and not thefts and events:
        # Only accept already-normalized detection-shaped events
        fills = [
            e
            for e in events
            if e.get("eventType") == "Fuel Filling" and _accepted_source(e.get("source"))
        ]
        thefts = [
            e
            for e in events
            if e.get("eventType") == "Fuel Theft" and _accepted_source(e.get("source"))
        ]

    def _vol(e):
        return abs(float(e.get("volume") or e.get("fuelIncrease") or e.get("fuelDecrease") or 0))

    total_filling = round(sum(_vol(e) for e in fills), 2)
    total_theft = round(sum(_vol(e) for e in thefts), 2)
    filling_count = len(fills)
    theft_count = len(thefts)

    ignition_on = int(latest.get("iO_1", 0) or 0) == 1
    speed = _safe_float(latest.get("speed", 0))
    if ignition_on and speed > 1.0:
        device_status = "Running"
    elif ignition_on:
        device_status = "Idle"
    else:
        device_status = format_parking_since(parking_streak) if parking_streak > 0 else "Parked"

    return {
        "deviceName": latest.get("deviceName", device_name),
        "imei": latest.get("imei"),
        "fuel": round(_safe_float(latest.get("fuelInLiter")), 2),
        "ignition": "On" if ignition_on else "Off",
        "ignitionStatus": 1 if ignition_on else 0,
        "speed": round(speed, 2),
        "deviceStatus": device_status,
        "totalTripKm": round(total_distance, 2),
        "trip": trip_count,
        "externalVoltage": round(_safe_float(latest.get("externalVoltage")), 2),
        "internalVoltage": round(_safe_float(latest.get("internalVoltage")), 2),
        "totalConsumption": round(total_consumption, 2),
        "totalFilling": total_filling,
        "fillingCount": filling_count,
        "totalFuelTakenOut": total_theft,
        "theftCount": theft_count,
        "totalRunningTime": int(running_time),
        "totalRunningTimeFormatted": format_running_time(running_time),
        "totalParkingTime": int(parking_streak),
        "averageConsumption": avg_consumption_lph,
        "totalDistance": round(total_distance, 2),
        "mileage": mileage,
        "latitude": latest.get("latitude"),
        "longitude": latest.get("longitude"),
        "latestTime": latest.get("time"),
        "currentFuel": round(_safe_float(latest.get("fuelInLiter")), 2),
        "totalTrip": trip_count,
    }


def series_point(entry: dict) -> dict:
    """Normalize a telemetry row for Live Chart / Live Table."""
    movement = entry.get("iO_2")
    if movement is None:
        movement = entry.get("iO_6", 0)
    return {
        "time": entry.get("time"),
        "fuelLevel": _safe_float(entry.get("fuelInLiter")),
        "fuelLevelEDC": int(entry.get("fuelEDC", 0) or 0),
        "ignition": int(entry.get("iO_1", 0) or 0),
        "movement": int(movement or 0),
        "speed": _safe_float(entry.get("speed")),
        "powerEx": int(entry.get("iO_66", 0) or 0),
        "powerIn": int(entry.get("iO_67", 0) or 0),
        "externalVoltage": _safe_float(entry.get("externalVoltage")),
        "internalVoltage": _safe_float(entry.get("internalVoltage")),
        "latitude": entry.get("latitude"),
        "longitude": entry.get("longitude"),
        "fuelInLiter": _safe_float(entry.get("fuelInLiter")),
        "iO_1": int(entry.get("iO_1", 0) or 0),
        "imei": entry.get("imei"),
        "deviceName": entry.get("deviceName"),
    }
