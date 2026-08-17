"""FuelArk API client — pull, normalize, and cache raw telemetry."""

from __future__ import annotations

import hashlib
import json
import os
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterable, List, Optional, Sequence, Union

import pandas as pd
import requests

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    from backports.zoneinfo import ZoneInfo  # type: ignore

import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config


CANONICAL_COLUMNS = [
    "timestamp",
    "imei",
    "vehicle_name_raw",
    "fuel_level",
    "fuel_level_edc",
    "ignition",
    "movement",
    "speed_kmh",
    "external_voltage",
    "internal_voltage",
    "engine_hours_delta",
    "distance_km",
    "parked_since",
    "soft_alert",
    "latitude",
    "longitude",
    # --- recovered from the API payload (previously discarded before persistence) ---
    "odometer_m",       # iO_16, metres, monotonic; exact distance without GPS guesswork
    "satellites",       # GPS fix quality; low counts make position/parked unreliable
    "distance_source",  # "odometer" | "gps" | "none" -- provenance for distance_km
    "engine_running",   # derived, not iO_1: ignition is provably stuck off on some devices
]


def _tz():
    return ZoneInfo(config.TIMEZONE)


def _parse_dt(value: Union[str, datetime]) -> datetime:
    if isinstance(value, datetime):
        dt = value
    else:
        text = str(value).strip().replace("Z", "+00:00")
        try:
            dt = datetime.fromisoformat(text)
        except ValueError:
            for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
                try:
                    dt = datetime.strptime(text, fmt)
                    break
                except ValueError:
                    continue
            else:
                raise ValueError(f"Unrecognized datetime: {value}")
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=_tz())
    else:
        dt = dt.astimezone(_tz())
    return dt


def _to_float(value, default=None):
    if value is None or value == "":
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _to_int(value, default=0):
    if value is None or value == "":
        return default
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _pick(row: dict, *keys, default=None):
    for key in keys:
        if key in row and row[key] is not None and row[key] != "":
            return row[key]
    lower = {str(k).lower(): v for k, v in row.items()}
    for key in keys:
        lk = key.lower()
        if lk in lower and lower[lk] is not None and lower[lk] != "":
            return lower[lk]
    return default


def normalize_records(raw_points: Sequence[dict], imei: str) -> pd.DataFrame:
    """Normalize FuelArk response rows into the canonical schema."""
    rows = []
    prev_ts = None
    prev_lat = None
    prev_lon = None
    prev_odo = None

    for row in raw_points:
        ts_raw = _pick(
            row,
            "deviceDataInsertTime",
            "DeviceDataInsertTime",
            "timestamp",
            "fromDatetime",
        )
        if not ts_raw:
            continue
        try:
            ts = _parse_dt(ts_raw)
        except ValueError:
            continue

        fuel = _to_float(_pick(row, "fuelInLiter", "FuelInLiter", "fuel_level"), 0.0)
        edc = _to_float(_pick(row, "iO_9", "IO_9", "fuelEDC", "fuel_level_edc"), None)
        ign_raw = _to_int(_pick(row, "iO_1", "IO_1", "ignition"), 0)
        ignition = "on" if ign_raw == 1 else "off"
        speed = _to_float(_pick(row, "speed", "Speed", "speed_kmh"), 0.0) or 0.0
        movement_raw = _pick(row, "iO_2", "IO_2", "iO_6", "movement")
        # Prefer accelerometer IO if present; else infer from speed
        if movement_raw is None:
            movement = bool(speed and speed > 0)
        else:
            movement = bool(_to_int(movement_raw, 0))

        ext_mv = _to_float(_pick(row, "iO_66", "IO_66", "externalVoltage"), None)
        int_mv = _to_float(_pick(row, "iO_67", "IO_67", "internalVoltage"), None)
        # FuelArk typically reports millivolts; values already in volts stay as-is
        if ext_mv is not None:
            external_voltage = ext_mv / 1000.0 if ext_mv > 100 else ext_mv
        else:
            external_voltage = None
        if int_mv is not None:
            internal_voltage = int_mv / 1000.0 if int_mv > 100 else int_mv
        else:
            internal_voltage = None

        lat = _to_float(_pick(row, "latitude", "Latitude"), None)
        lon = _to_float(_pick(row, "longitude", "Longitude"), None)
        odometer_m = _to_float(_pick(row, "iO_16", "IO_16", "odometer_m"), None)
        satellites = _to_float(_pick(row, "satellites", "Satellites"), None)

        # Distance and engine-hours deltas between consecutive samples.
        distance_km = 0.0
        engine_hours_delta = 0.0
        distance_source = "none"
        odo_delta_m = None

        if prev_ts is not None:
            dt_hours = max((ts - prev_ts).total_seconds() / 3600.0, 0.0)

            # Odometer is exact where present; prefer it over inferring from GPS.
            # Guard against counter resets (negative) and implausible jumps: 200 km/h
            # over the sample interval, with a floor so tiny gaps stay tolerant.
            if odometer_m is not None and prev_odo is not None:
                delta = odometer_m - prev_odo
                max_plausible_m = max(200.0 * dt_hours * 1000.0, 2000.0)
                if 0 <= delta <= max_plausible_m:
                    odo_delta_m = delta
                    distance_km = delta / 1000.0
                    distance_source = "odometer"

            if distance_source == "none" and (
                movement
                and speed > 0
                and prev_lat is not None
                and prev_lon is not None
                and lat is not None
                and lon is not None
            ):
                # Haversine km
                from math import radians, cos, sin, asin, sqrt

                rlat1, rlon1, rlat2, rlon2 = map(radians, [prev_lat, prev_lon, lat, lon])
                dlat = rlat2 - rlat1
                dlon = rlon2 - rlon1
                a = sin(dlat / 2) ** 2 + cos(rlat1) * cos(rlat2) * sin(dlon / 2) ** 2
                distance_km = 2 * 6371.0 * asin(sqrt(min(a, 1.0)))
                # Cap GPS jump noise
                if distance_km > speed * dt_hours * 1.5 + 0.5:
                    distance_km = max(speed, 0.0) * dt_hours
                distance_source = "gps"

        # `ignition` (iO_1) is stuck off on some devices while they are demonstrably
        # driving -- one logged 1,015 moving ticks and 119.8 km with iO_1 == 0 all day.
        # Trusting it means expected burn is never subtracted, so ordinary consumption
        # reads as a drain. Derive the running state from evidence of motion instead.
        engine_running = bool(
            ignition == "on" or speed > 0 or (odo_delta_m is not None and odo_delta_m > 0)
        )
        if prev_ts is not None and engine_running:
            engine_hours_delta = max((ts - prev_ts).total_seconds() / 3600.0, 0.0)

        soft_alert = _pick(row, "softAlert", "SoftAlert", "alert", "Alert", default=None)
        parked_since = _pick(row, "parkedSince", "ParkedSince", "parked_since", default=None)
        vehicle_name = _pick(row, "deviceName", "DeviceName", "vehicle_name", default=None)
        imei_val = str(_pick(row, "imei", "IMEI", default=imei) or imei)

        rows.append(
            {
                "timestamp": ts,
                "imei": imei_val,
                "vehicle_name_raw": vehicle_name,
                "fuel_level": fuel,
                "fuel_level_edc": edc,
                "ignition": ignition,
                "movement": movement,
                "speed_kmh": speed,
                "external_voltage": external_voltage,
                "internal_voltage": internal_voltage,
                "engine_hours_delta": engine_hours_delta,
                "distance_km": distance_km,
                "parked_since": parked_since,
                "soft_alert": soft_alert,
                "latitude": lat,
                "longitude": lon,
                "odometer_m": odometer_m,
                "satellites": satellites,
                "distance_source": distance_source,
                "engine_running": engine_running,
            }
        )
        prev_ts = ts
        prev_lat = lat
        prev_lon = lon
        if odometer_m is not None:
            prev_odo = odometer_m

    if not rows:
        return pd.DataFrame(columns=CANONICAL_COLUMNS)

    df = pd.DataFrame(rows).sort_values("timestamp").drop_duplicates(
        subset=["timestamp", "imei"], keep="last"
    )
    return df.reset_index(drop=True)


def _cache_key(imei: str, start: datetime, end: datetime) -> str:
    stamp = f"{imei}_{start.date()}_{end.date()}"
    digest = hashlib.md5(stamp.encode()).hexdigest()[:8]
    return f"{imei}_{start.strftime('%Y%m%d')}_{end.strftime('%Y%m%d')}_{digest}"


def cache_path(imei: str, start: datetime, end: datetime, fmt: str = "parquet") -> Path:
    Path(config.CACHE_DIR).mkdir(parents=True, exist_ok=True)
    return Path(config.CACHE_DIR) / f"{_cache_key(imei, start, end)}.{fmt}"


def load_cache(imei: str, start: datetime, end: datetime) -> Optional[pd.DataFrame]:
    for fmt in ("parquet", "csv"):
        path = cache_path(imei, start, end, fmt=fmt)
        if not path.exists():
            continue
        if fmt == "parquet":
            try:
                df = pd.read_parquet(path)
            except Exception:
                continue
        else:
            df = pd.read_csv(path, parse_dates=["timestamp"])
        if "timestamp" in df.columns:
            df["timestamp"] = pd.to_datetime(df["timestamp"], utc=False)
            if df["timestamp"].dt.tz is None:
                df["timestamp"] = df["timestamp"].dt.tz_localize(_tz())
            else:
                df["timestamp"] = df["timestamp"].dt.tz_convert(_tz())
        return df
    return None


def save_cache(df: pd.DataFrame, imei: str, start: datetime, end: datetime) -> Path:
    Path(config.CACHE_DIR).mkdir(parents=True, exist_ok=True)
    pq = cache_path(imei, start, end, fmt="parquet")
    csv = cache_path(imei, start, end, fmt="csv")
    try:
        df.to_parquet(pq, index=False)
        return pq
    except Exception:
        df.to_csv(csv, index=False)
        return csv


def _api_key() -> str:
    key = config.FUELARK_API_KEY or os.environ.get("FUELARK_API_KEY", "")
    if not key:
        raise RuntimeError(
            "FUELARK_API_KEY is not set. Export it or copy .env.example → set the key."
        )
    return key


def fetch_day(imei: str, day: Union[str, datetime], *, use_cache: bool = True) -> pd.DataFrame:
    """Fetch one calendar day for an IMEI."""
    day_dt = _parse_dt(day).replace(hour=0, minute=0, second=0, microsecond=0)
    start = day_dt
    end = day_dt.replace(hour=23, minute=59, second=59)
    return fetch_range(imei, start, end, use_cache=use_cache)


def _request_day_raw(imei: str, day_str: str) -> List[dict]:
    headers = {"x-api-key": _api_key(), "Content-Type": "application/json"}
    params = {
        "fromdate": f"{day_str} 00:00:00",
        "todate": f"{day_str} 23:59:59",
        "IMEI": imei,
    }
    last_err = None
    for attempt in range(config.API_MAX_RETRIES):
        try:
            resp = requests.get(
                config.FUELARK_API_URL, headers=headers, params=params, timeout=90
            )
            resp.raise_for_status()
            payload = resp.json()
            if payload.get("statusCode") == 1:
                return payload.get("responseData", {}).get("deviceFuelInfo", []) or []
            if payload.get("StatusCode") == 200:
                return payload.get("ResponseData", {}).get("DeviceFuelInfo", []) or []
            return []
        except requests.RequestException as exc:
            last_err = exc
            time.sleep(config.API_RETRY_BASE_SECONDS * (2 ** attempt))
    if last_err:
        raise RuntimeError(f"FuelArk API failed for {imei} on {day_str}: {last_err}")
    return []


def fetch_range(
    imei: str,
    start: Union[str, datetime],
    end: Union[str, datetime],
    *,
    use_cache: bool = True,
) -> pd.DataFrame:
    """Fetch telemetry for an IMEI between start and end (inclusive by day)."""
    start_dt = _parse_dt(start)
    end_dt = _parse_dt(end)
    if end_dt < start_dt:
        start_dt, end_dt = end_dt, start_dt

    if use_cache:
        cached = load_cache(imei, start_dt, end_dt)
        if cached is not None and not cached.empty:
            mask = (cached["timestamp"] >= start_dt) & (cached["timestamp"] <= end_dt)
            return cached.loc[mask].reset_index(drop=True)

    all_raw: List[dict] = []
    day = start_dt.date()
    last = end_dt.date()
    while day <= last:
        raw = _request_day_raw(imei, day.isoformat())
        all_raw.extend(raw)
        time.sleep(config.API_REQUEST_GAP_SECONDS)
        day += timedelta(days=1)

    df = normalize_records(all_raw, imei=str(imei))
    if not df.empty:
        mask = (df["timestamp"] >= start_dt) & (df["timestamp"] <= end_dt)
        df = df.loc[mask].reset_index(drop=True)
        if use_cache:
            save_cache(df, imei, start_dt, end_dt)
    return df


def fetch_batch(
    imeis: Iterable[str],
    start: Union[str, datetime],
    end: Union[str, datetime],
    *,
    use_cache: bool = True,
) -> pd.DataFrame:
    frames = []
    for imei in imeis:
        imei = str(imei).strip()
        if not imei:
            continue
        try:
            df = fetch_range(imei, start, end, use_cache=use_cache)
            if not df.empty:
                frames.append(df)
                print(f"[fetch] {imei}: {len(df)} points")
            else:
                print(f"[fetch] {imei}: no data")
        except Exception as exc:
            print(f"[fetch] {imei}: ERROR {exc}")
    if not frames:
        return pd.DataFrame(columns=CANONICAL_COLUMNS)
    return pd.concat(frames, ignore_index=True).sort_values(["imei", "timestamp"])


def load_local_json(path: str, imei: Optional[str] = None) -> pd.DataFrame:
    """Load previously saved FuelArk JSON (e.g. from the parent project) without API."""
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if imei:
        data = [r for r in data if str(r.get("imei") or "") == str(imei)]
        return normalize_records(data, imei=str(imei))
    # Group by imei when present
    by_imei = {}
    for row in data:
        key = str(row.get("imei") or row.get("deviceName") or "unknown")
        by_imei.setdefault(key, []).append(row)
    frames = [normalize_records(rows, imei=k) for k, rows in by_imei.items()]
    if not frames:
        return pd.DataFrame(columns=CANONICAL_COLUMNS)
    return pd.concat(frames, ignore_index=True)
