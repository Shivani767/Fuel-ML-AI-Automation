"""IMEI → vehicle_name history join (time-aware, never static-only)."""

from __future__ import annotations

import os
from datetime import datetime
from typing import Optional, Union

import pandas as pd

import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    from backports.zoneinfo import ZoneInfo  # type: ignore


ASSIGNMENT_COLUMNS = ["imei", "vehicle_name", "valid_from", "valid_to", "vehicle_type"]


def _tz():
    return ZoneInfo(config.TIMEZONE)


def _as_ts(value) -> Optional[pd.Timestamp]:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    if isinstance(value, str) and value.strip() == "":
        return None
    ts = pd.to_datetime(value)
    if getattr(ts, "tzinfo", None) is None and getattr(ts, "tz", None) is None:
        try:
            ts = ts.tz_localize(_tz())
        except TypeError:
            ts = pd.Timestamp(ts).tz_localize(_tz())
    else:
        ts = pd.Timestamp(ts).tz_convert(_tz())
    return ts


def load_assignments(path: Optional[str] = None) -> pd.DataFrame:
    path = path or config.ASSIGNMENTS_CSV
    if not os.path.exists(path):
        return pd.DataFrame(columns=ASSIGNMENT_COLUMNS)

    df = pd.read_csv(path)
    # Accept IMEI / Vehicle_Name style headers from external CSVs
    rename = {}
    if "IMEI" in df.columns and "imei" not in df.columns:
        rename["IMEI"] = "imei"
    if "Vehicle_Name" in df.columns and "vehicle_name" not in df.columns:
        rename["Vehicle_Name"] = "vehicle_name"
    if "vehicle" in df.columns and "vehicle_name" not in df.columns:
        rename["vehicle"] = "vehicle_name"
    if rename:
        df = df.rename(columns=rename)
    if "imei" in df.columns:
        df["imei"] = df["imei"].astype(str)
    for col in ASSIGNMENT_COLUMNS:
        if col not in df.columns:
            df[col] = None if col != "vehicle_type" else "default"
    df["imei"] = df["imei"].astype(str)
    df["valid_from"] = df["valid_from"].apply(_as_ts)
    df["valid_to"] = df["valid_to"].apply(_as_ts)
    df["vehicle_type"] = df["vehicle_type"].fillna("default")
    return df.sort_values(["imei", "valid_from"]).reset_index(drop=True)


def resolve_vehicle(
    imei: str,
    at: Union[str, datetime, pd.Timestamp],
    assignments: Optional[pd.DataFrame] = None,
) -> dict:
    """Return vehicle_name / vehicle_type for an IMEI at timestamp `at`."""
    assignments = load_assignments() if assignments is None else assignments
    at_ts = _as_ts(at)
    imei = str(imei)
    rows = assignments[assignments["imei"] == imei]
    if rows.empty:
        return {
            "imei": imei,
            "vehicle_name": None,
            "vehicle_type": "default",
            "assignment_matched": False,
        }

    matched = None
    for _, row in rows.iterrows():
        start = row["valid_from"]
        end = row["valid_to"]
        if start is not None and at_ts < start:
            continue
        if end is not None and at_ts > end:
            continue
        matched = row
        break

    if matched is None:
        # Fall back to latest assignment starting before `at`
        prior = rows[rows["valid_from"].isna() | (rows["valid_from"] <= at_ts)]
        if not prior.empty:
            matched = prior.iloc[-1]

    if matched is None:
        return {
            "imei": imei,
            "vehicle_name": None,
            "vehicle_type": "default",
            "assignment_matched": False,
        }

    return {
        "imei": imei,
        "vehicle_name": matched["vehicle_name"],
        "vehicle_type": matched.get("vehicle_type") or "default",
        "assignment_matched": True,
        "valid_from": matched["valid_from"],
        "valid_to": matched["valid_to"],
    }


def attach_vehicle_names(
    telemetry: pd.DataFrame,
    assignments: Optional[pd.DataFrame] = None,
) -> pd.DataFrame:
    """Add vehicle_name / vehicle_type columns resolved per row timestamp."""
    if telemetry.empty:
        out = telemetry.copy()
        out["vehicle_name"] = []
        out["vehicle_type"] = []
        return out

    assignments = load_assignments() if assignments is None else assignments
    resolved_name = []
    resolved_type = []
    for _, row in telemetry.iterrows():
        info = resolve_vehicle(row["imei"], row["timestamp"], assignments)
        # Prefer assignment table; fall back to API deviceName for unknown IMEIs
        name = info["vehicle_name"] or row.get("vehicle_name_raw")
        resolved_name.append(name)
        resolved_type.append(info["vehicle_type"])
    out = telemetry.copy()
    out["vehicle_name"] = resolved_name
    out["vehicle_type"] = resolved_type
    return out


def upsert_assignment(
    imei: str,
    vehicle_name: str,
    valid_from: Union[str, datetime],
    valid_to: Optional[Union[str, datetime]] = None,
    vehicle_type: str = "default",
    path: Optional[str] = None,
) -> pd.DataFrame:
    """Append/update an assignment row and persist CSV."""
    path = path or config.ASSIGNMENTS_CSV
    df = load_assignments(path)
    # Close previous open-ended assignment for this IMEI
    vf = _as_ts(valid_from)
    open_mask = (df["imei"] == str(imei)) & df["valid_to"].isna()
    if open_mask.any() and vf is not None:
        df.loc[open_mask, "valid_to"] = vf - pd.Timedelta(seconds=1)

    new_row = {
        "imei": str(imei),
        "vehicle_name": vehicle_name,
        "valid_from": vf,
        "valid_to": _as_ts(valid_to),
        "vehicle_type": vehicle_type or "default",
    }
    df = pd.concat([df, pd.DataFrame([new_row])], ignore_index=True)
    out = df.copy()
    out["valid_from"] = out["valid_from"].apply(
        lambda x: x.isoformat() if pd.notna(x) and x is not None else ""
    )
    out["valid_to"] = out["valid_to"].apply(
        lambda x: x.isoformat() if pd.notna(x) and x is not None else ""
    )
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    out.to_csv(path, index=False)
    return load_assignments(path)
