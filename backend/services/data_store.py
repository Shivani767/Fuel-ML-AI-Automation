"""In-memory fuel telemetry store loaded from fuel_data/."""

from __future__ import annotations

import gzip
import json
import sys
import threading
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional

from backend import config
from backend.services.vehicle_aliases import spreadsheet_to_device, to_display_name

_lock = threading.Lock()
device_data_dict: Dict[str, List[dict]] = defaultdict(list)
device_imei_map: Dict[str, str] = {}
_loaded = False
_loading = False
_load_error: Optional[str] = None
_load_started_at: Optional[float] = None


def is_ready() -> bool:
    return _loaded


def is_loading() -> bool:
    return _loading and not _loaded


def load_status() -> dict:
    import time

    return {
        "ready": _loaded,
        "loading": is_loading(),
        "error": _load_error,
        "devices": len(device_data_dict) if _loaded else 0,
        "points": sum(len(v) for v in device_data_dict.values()) if _loaded else 0,
        "elapsedSec": (
            round(time.time() - _load_started_at, 1) if _load_started_at else None
        ),
    }


def _project_root_on_path():
    root = str(config.PROJECT_ROOT)
    if root not in sys.path:
        sys.path.insert(0, root)


def _read_json_list(path: Path) -> list:
    print(f"[backend] Loading {path.name}…", flush=True)
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, list):
        raise ValueError(f"{path} must be a JSON array")
    print(f"[backend]   → {len(data):,} rows from {path.name}", flush=True)
    return data


def _month_dump_path(stem: str) -> Path:
    """Map 'may' / 'june' → fuel_data/may_2026_data.json etc."""
    stem = stem.strip().lower()
    known = {
        "may": getattr(config, "MAY_DATA_PATH", config.FUEL_DATA_DIR / "may_2026_data.json"),
        "june": config.JUNE_DATA_PATH,
    }
    if stem in known:
        return known[stem]
    return config.FUEL_DATA_DIR / f"{stem}_2026_data.json"


def load_daily_days(start: date, end: date) -> list:
    """Load capture store days (*.json.gz / *.json). IMEI comes from filename when missing."""
    daily_dir = getattr(config, "DAILY_DIR", config.FUEL_DATA_DIR / "daily")
    if not daily_dir.is_dir():
        return []
    out: list = []
    day = start
    while day <= end:
        day_dir = daily_dir / day.isoformat()
        day += timedelta(days=1)
        if not day_dir.is_dir():
            continue
        files = list(day_dir.glob("*.json.gz"))
        seen_imeis = {p.name.replace(".json.gz", "") for p in files}
        for path in day_dir.glob("*.json"):
            if path.stem not in seen_imeis:
                files.append(path)
        files = sorted(files, key=lambda p: p.name)
        for path in files:
            imei = path.name.replace(".json.gz", "").replace(".json", "")
            opener = gzip.open if path.name.endswith(".json.gz") else open
            try:
                with opener(path, "rt", encoding="utf-8") as f:
                    rows = json.load(f)
            except (OSError, ValueError, json.JSONDecodeError) as exc:
                print(f"[backend] skip {path.name}: {exc}", flush=True)
                continue
            if not isinstance(rows, list):
                continue
            for row in rows:
                if not row.get("imei"):
                    row["imei"] = imei
            out.extend(rows)
    print(
        f"[backend] Daily {start.isoformat()}→{end.isoformat()}: {len(out):,} rows",
        flush=True,
    )
    return out


def load_raw_fuel_data() -> list:
    """
    Merge May + June dumps, early-July daily captures, and the live overlay
    so Live Map / Alerts can chart May–July (and current) for all vehicles.
    """
    data: list = []
    loaded_any = False

    for stem in getattr(config, "LOAD_MONTH_DUMPS", ["may", "june"]):
        path = _month_dump_path(stem)
        if path.exists():
            data.extend(_read_json_list(path))
            loaded_any = True
        else:
            print(f"[backend] Month dump missing (skip): {path.name}", flush=True)

    # Live overlay starts ~Jul 18; fill Jul 1–17 from nightly daily/ store.
    july_gap = load_daily_days(date(2026, 7, 1), date(2026, 7, 17))
    if july_gap:
        data.extend(july_gap)
        loaded_any = True

    live = config.LIVE_DATA_PATH
    if live.exists():
        print(f"[backend] Merging live overlay from {live.name}", flush=True)
        data.extend(_read_json_list(live))
        loaded_any = True

    if not loaded_any:
        combined = config.FUEL_DATA_DIR / "all_fuel_data_combined.json"
        if combined.exists():
            data.extend(_read_json_list(combined))
            loaded_any = True

    if not data:
        raise FileNotFoundError(
            f"No fuel data under {config.FUEL_DATA_DIR}. "
            "Need may/june dumps, daily/, or live_fuel_data.json."
        )
    print(f"[backend] Raw merge total: {len(data):,} rows", flush=True)
    return data


def parse_data(data: list) -> List[dict]:
    parsed_entries = []
    for entry in data:
        try:
            time_str = entry["deviceDataInsertTime"]
            if "." in time_str:
                parts = time_str.split(".")
                if len(parts[1]) < 3:
                    parts[1] = parts[1].ljust(3, "0")
                time_str = ".".join(parts)
            imei = str(entry["imei"]) if entry.get("imei") else None
            raw_name = str(entry["deviceName"]).strip()
            parsed_entries.append(
                {
                    "deviceId": entry.get("deviceInfoId"),
                    "deviceName": to_display_name(raw_name, imei),
                    "imei": imei,
                    "time": time_str,
                    "fuelInLiter": float(entry["fuelInLiter"]) if entry.get("fuelInLiter") else 0,
                    "fuelEDC": int(entry["iO_9"]) if entry.get("iO_9") else 0,
                    "iO_1": int(entry["iO_1"]) if entry.get("iO_1") else 0,
                    "iO_2": int(entry["iO_2"]) if entry.get("iO_2") else 0,
                    "iO_6": int(entry["iO_6"]) if entry.get("iO_6") else 0,
                    "iO_66": int(entry["iO_66"]) if entry.get("iO_66") else 0,
                    "iO_67": int(entry["iO_67"]) if entry.get("iO_67") else 0,
                    "speed": float(entry["speed"]) if entry.get("speed") else 0,
                    "latitude": entry.get("latitude"),
                    "longitude": entry.get("longitude"),
                    "externalVoltage": float(entry["iO_66"]) / 1000 if entry.get("iO_66") else 0,
                    "internalVoltage": float(entry["iO_67"]) / 1000 if entry.get("iO_67") else 0,
                }
            )
        except Exception:
            continue
    return parsed_entries


def persist_imei_map():
    config.FUEL_DATA_DIR.mkdir(parents=True, exist_ok=True)
    with open(config.DEVICE_IMEI_MAP_PATH, "w", encoding="utf-8") as f:
        json.dump(device_imei_map, f, ensure_ascii=False, indent=2)


def load_imei_map_file():
    if config.DEVICE_IMEI_MAP_PATH.exists():
        with open(config.DEVICE_IMEI_MAP_PATH, "r", encoding="utf-8") as f:
            raw = json.load(f)
        for name, imei in raw.items():
            device_imei_map[str(name).strip()] = str(imei)


def ensure_loaded(force: bool = False):
    global _loaded, _loading, _load_error, _load_started_at
    import time

    with _lock:
        if _loaded and not force:
            return
        _loading = True
        _load_error = None
        _load_started_at = time.time()
        try:
            device_data_dict.clear()
            raw = load_raw_fuel_data()
            parsed = parse_data(raw)
            for entry in parsed:
                device_data_dict[entry["deviceName"]].append(entry)
                if entry.get("imei") and entry.get("deviceName"):
                    device_imei_map[entry["deviceName"]] = str(entry["imei"])
            for name in device_data_dict:
                device_data_dict[name].sort(key=lambda x: x["time"])
            load_imei_map_file()
            _loaded = True
            print(
                f"[backend] Ready: {len(device_data_dict)} devices, "
                f"{sum(len(v) for v in device_data_dict.values())} points, "
                f"{len(device_imei_map)} IMEI mappings",
                flush=True,
            )
        except Exception as e:
            _load_error = str(e)
            _loaded = False
            print(f"[backend] Load failed: {e}", flush=True)
            raise
        finally:
            _loading = False


def start_background_load(on_ready=None):
    """Load May–July dumps without blocking Flask bind (avoids Vite 502)."""
    if _loaded or _loading:
        return

    def _run():
        try:
            ensure_loaded()
            if on_ready:
                on_ready()
        except Exception:
            pass

    threading.Thread(target=_run, name="fuel-data-load", daemon=True).start()


def load_events() -> list:
    if not config.EVENTS_PATH.exists():
        return []
    with open(config.EVENTS_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def load_ml_alerts() -> list:
    if not config.ML_ALERTS_PATH.exists():
        return []
    with open(config.ML_ALERTS_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def parse_date_param(date_str: Optional[str]):
    if not date_str:
        return None
    try:
        return datetime.fromisoformat(date_str.replace("Z", "+00:00").split("+")[0])
    except ValueError:
        for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d / %m / %Y"):
            try:
                return datetime.strptime(date_str.strip(), fmt)
            except ValueError:
                continue
    return None


def apply_date_filter(entries, from_date, to_date, time_key="time"):
    if not from_date and not to_date:
        return entries
    filtered = []
    for entry in entries:
        try:
            entry_time = datetime.fromisoformat(entry[time_key])
        except Exception:
            continue
        if from_date and entry_time < from_date:
            continue
        if to_date and entry_time > to_date:
            continue
        filtered.append(entry)
    return filtered


def resolve_device_name(name: Optional[str]) -> Optional[str]:
    """Match a device name allowing trim / case-insensitive / display alias."""
    ensure_loaded()
    if not name:
        return None
    raw = str(name)
    if raw in device_data_dict:
        return raw
    stripped = raw.strip()
    if stripped in device_data_dict:
        return stripped
    # Spreadsheet / short API name → Fuelark display name
    aliased = spreadsheet_to_device(stripped)
    if aliased != stripped and aliased in device_data_dict:
        return aliased
    if aliased.strip() in device_data_dict:
        return aliased.strip()
    display = to_display_name(stripped)
    if display != stripped and display in device_data_dict:
        return display
    lower = stripped.lower()
    for key in device_data_dict:
        if key.strip().lower() == lower:
            return key
    for candidate in (aliased, display):
        alias_lower = candidate.strip().lower()
        for key in device_data_dict:
            if key.strip().lower() == alias_lower:
                return key
    # partial: unique substring match
    hits = [k for k in device_data_dict if lower and lower in k.lower()]
    if len(hits) == 1:
        return hits[0]
    return None


def get_device_series(name: Optional[str]) -> List[dict]:
    resolved = resolve_device_name(name)
    if not resolved:
        return []
    return device_data_dict.get(resolved, [])


def resolve_imei(device_name: str) -> Optional[str]:
    ensure_loaded()
    resolved = resolve_device_name(device_name) or device_name
    if resolved in device_imei_map:
        return device_imei_map[resolved]
    for entry in device_data_dict.get(resolved, []):
        if entry.get("imei"):
            device_imei_map[resolved] = entry["imei"]
            persist_imei_map()
            return entry["imei"]
    return None


def merge_live_points(points: List[dict]) -> int:
    ensure_loaded()
    added = 0
    by_device = defaultdict(list)
    for entry in points:
        imei = str(entry["imei"]) if entry.get("imei") else None
        name = to_display_name(str(entry.get("deviceName") or "").strip(), imei)
        entry = {**entry, "deviceName": name, "imei": imei}
        by_device[name].append(entry)
        if imei and name:
            device_imei_map[name] = imei

    for device_name, new_points in by_device.items():
        existing = device_data_dict[device_name]
        existing_times = {p["time"] for p in existing}
        fresh = [p for p in new_points if p["time"] not in existing_times]
        if fresh:
            existing.extend(fresh)
            existing.sort(key=lambda x: x["time"])
            added += len(fresh)
    if by_device:
        persist_imei_map()
    return added


def _load_fleet_roster() -> List[str]:
    """Canonical Fuelark Vehicle Summary names (Total Vehicle)."""
    path = getattr(config, "ROSTER_PATH", config.FUEL_DATA_DIR / "fuelark_vehicle_roster.json")
    if path.exists():
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(raw, list):
                return [str(n).strip() for n in raw if str(n).strip()]
        except Exception:
            pass
    return []


def _series_for_display_name(name: str) -> List[dict]:
    """Resolve telemetry series for a roster/display name (incl. aliases)."""
    series = device_data_dict.get(name) or []
    if series:
        return series
    # Alias keys in memory that map to this display name
    for alt, pts in device_data_dict.items():
        if not pts:
            continue
        if to_display_name(alt) == name or alt == name:
            if len(pts) > len(series):
                series = pts
    return series


def fleet_device_cards(inactive_hours: float = None, today_only: bool = True) -> List[dict]:
    """Build fleet cards for KPI + list (Fuelark Vehicle Summary semantics).

    Total: Fuelark roster when present, else IMEI map ∪ devices with data.
    Active: last telemetry within ACTIVE_ONLINE_MINUTES and not battery-disconnected.
    Running: Active + ignition ON + moving (speed or movement IO) — matches FuelArk
    Vehicle Summary (idle ignition-on excavators/gensets are Active, not Running).
    Inactive: Total − Active (includes battery-disconnected soft-alert units).
    """
    ensure_loaded()
    inactive_hours = inactive_hours if inactive_hours is not None else config.INACTIVE_HOURS
    online_min = float(getattr(config, "ACTIVE_ONLINE_MINUTES", 15))
    batt_mv = float(getattr(config, "BATTERY_DISCONNECT_MV", 11000))
    run_speed = float(getattr(config, "RUNNING_SPEED_KMH", 1.0))
    try:
        from zoneinfo import ZoneInfo

        now = datetime.now(ZoneInfo("Asia/Kolkata")).replace(tzinfo=None)
    except Exception:
        now = datetime.now()
    today = now.date()

    roster = _load_fleet_roster()
    if roster:
        names = set(roster)
    else:
        names = set(device_data_dict.keys()) | set(device_imei_map.keys())

    by_imei_best = {}
    cards_tmp = []

    for name in sorted(names):
        series = _series_for_display_name(name)
        latest = series[-1] if series else None
        imei = resolve_imei(name) or (device_imei_map.get(name) if name in device_imei_map else None)
        if not imei and series:
            imei = series[-1].get("imei")

        io66 = 0
        movement = False
        if latest:
            try:
                last_seen = datetime.fromisoformat(str(latest["time"]))
                if getattr(last_seen, "tzinfo", None):
                    last_seen = last_seen.replace(tzinfo=None)
                age_h = (now - last_seen).total_seconds() / 3600.0
                age_m = age_h * 60.0
                seen_today = last_seen.date() == today
            except Exception:
                last_seen = None
                age_h = 9999.0
                age_m = 9999.0 * 60
                seen_today = False
            ignition = int(latest.get("iO_1", 0) or 0)
            speed = float(latest.get("speed", 0) or 0)
            movement = bool(int(latest.get("iO_2", 0) or latest.get("iO_6", 0) or 0))
            fuel = round(float(latest.get("fuelInLiter", 0) or 0), 2)
            lat, lon = latest.get("latitude"), latest.get("longitude")
            last_seen_s = latest.get("time")
            io66 = int(latest.get("iO_66", 0) or 0)
            if not io66 and latest.get("externalVoltage"):
                io66 = int(float(latest.get("externalVoltage") or 0) * 1000)
        else:
            last_seen = None
            age_h = 9999.0
            age_m = 9999.0 * 60
            seen_today = False
            ignition = 0
            speed = 0.0
            fuel = None
            lat = lon = None
            last_seen_s = None

        online = bool(latest) and age_m <= online_min
        # Soft-alert battery only while the device is currently online (Fuelark-style).
        # Stale historical low-voltage points must not steal Inactive count.
        battery_disconnected = bool(online and io66 < batt_mv)
        if today_only:
            # Fuelark dashboard: Active ≈ currently online, not merely "seen today"
            active = bool(online and not battery_disconnected)
        else:
            active = bool(age_h <= inactive_hours and not (io66 < batt_mv))
        # FuelArk Running = reporting speed > floor (default 0). Ignition not required.
        if run_speed <= 0:
            moving = speed > 0 or bool(movement)
        else:
            moving = speed > run_speed or bool(movement)
        running = bool(active and moving)

        card = {
            "deviceName": name,
            "imei": str(imei) if imei else None,
            "lastSeen": last_seen_s,
            "fuel": fuel,
            "speed": round(speed, 2),
            "ignition": "On" if ignition else "Off",
            "ignitionStatus": ignition,
            "movement": movement,
            "auxIgnition": "Off",
            "externalVoltage": round(io66 / 1000.0, 2) if io66 else 0,
            "batteryDisconnected": battery_disconnected,
            "latitude": lat,
            "longitude": lon,
            "active": active,
            "running": running,
            "seenToday": seen_today,
            "online": online,
            "signal": "ok" if active else ("battery" if battery_disconnected else "weak"),
            "progress": 100 if active else max(0, int(100 - min(age_h, 100))),
            "status": (
                "battery"
                if battery_disconnected
                else ("running" if running else ("active" if active else "inactive"))
            ),
            "_age_h": age_h,
            "_last_seen_dt": last_seen,
        }
        cards_tmp.append(card)

        if imei:
            prev = by_imei_best.get(str(imei))
            if prev is None or (card["_last_seen_dt"] or datetime.min) > (
                prev.get("_last_seen_dt") or datetime.min
            ):
                by_imei_best[str(imei)] = card

    # Always one card per IMEI (roster short-names + full names share devices)
    if by_imei_best:
        used_imeis = set()
        cards = []
        for card in sorted(cards_tmp, key=lambda c: c["deviceName"]):
            imei = card.get("imei")
            if not imei:
                cards.append(card)
                continue
            if imei in used_imeis:
                continue
            best = by_imei_best.get(imei, card)
            cards.append(best)
            used_imeis.add(imei)
    else:
        cards = cards_tmp

    for c in cards:
        c.pop("_age_h", None)
        c.pop("_last_seen_dt", None)
    return cards


def fleet_summary(
    cards: Optional[List[dict]] = None,
    *,
    fuelark_kpis: Optional[dict] = None,
) -> dict:
    try:
        from zoneinfo import ZoneInfo

        now = datetime.now(ZoneInfo("Asia/Kolkata")).replace(tzinfo=None)
    except Exception:
        now = datetime.now()
    cards = cards if cards is not None else fleet_device_cards(today_only=True)
    battery_n = sum(1 for c in cards if c.get("batteryDisconnected"))
    active_n = sum(1 for c in cards if c["active"])
    running_n = sum(1 for c in cards if c["running"])
    # FuelArk-style: every vehicle is Active or Inactive. Battery-disconnected is a
    # soft-alert on top (those units are still "not active"), not a third KPI bucket
    # that disappears from Inactive and makes the card look wrong.
    inactive_n = sum(1 for c in cards if not c["active"])
    total_n = len(cards)
    kpi_source = "local_telemetry"

    # Prefer FuelArk Helper/DeviceStatus — same counters as web1 Home / Vehicle Summary.
    if fuelark_kpis and fuelark_kpis.get("totalDevices"):
        total_n = int(fuelark_kpis["totalDevices"])
        active_n = int(fuelark_kpis["active"])
        running_n = int(fuelark_kpis["running"])
        battery_n = int(fuelark_kpis.get("batteryRemove") or battery_n)
        # Match FuelArk Home: InActive is offline-without-battery; battery is separate.
        # Prefer official inActive; fall back to Total − Active − Battery.
        if fuelark_kpis.get("inActive") is not None:
            inactive_n = int(fuelark_kpis["inActive"])
        else:
            inactive_n = max(0, total_n - active_n - battery_n)
        kpi_source = str(fuelark_kpis.get("source") or "fuelark_device_status")

    offline_n = inactive_n
    online_min = float(getattr(config, "ACTIVE_ONLINE_MINUTES", 5))
    run_speed = float(getattr(config, "RUNNING_SPEED_KMH", 0))
    run_rule = (
        "speed > 0 km/h (FuelArk Live)"
        if run_speed <= 0
        else f"speed > {run_speed:g} km/h or movement IO"
    )
    return {
        "asOfDate": now.strftime("%Y-%m-%d"),
        "asOfTime": now.strftime("%H:%M:%S"),
        "asOfLabel": now.strftime("%d %b %Y"),
        "timezone": "Asia/Kolkata",
        "totalVehicle": total_n,
        "activeVehicle": active_n,
        "runningVehicle": running_n,
        "inactiveVehicle": inactive_n,
        "batteryDisconnected": battery_n,
        "idleVehicle": max(0, active_n - running_n),
        "kpiSource": kpi_source,
        "fuelarkInActive": (fuelark_kpis or {}).get("inActive"),
        "chart": {
            "labels": ["Active", "Running", "Inactive", "Battery"],
            "values": [active_n, running_n, inactive_n, battery_n],
            "segments": [
                {"key": "running", "label": "Running", "value": running_n, "color": "#22c55e"},
                {
                    "key": "idle",
                    "label": "Active (idle)",
                    "value": max(0, active_n - running_n),
                    "color": "#0ea5e9",
                },
                {"key": "inactive", "label": "Inactive", "value": offline_n, "color": "#f97316"},
                {
                    "key": "battery",
                    "label": "Battery disconnected",
                    "value": battery_n,
                    "color": "#7c3aed",
                },
            ],
        },
        "definitions": {
            "totalVehicle": (
                "Unique vehicles on the FuelArk account roster (same as Home)."
            ),
            "activeVehicle": (
                f"Online now — last live update within {online_min:.0f} minutes, "
                "excluding battery-remove (FuelArk Active)."
            ),
            "runningVehicle": (
                f"Active and moving ({run_rule}) — FuelArk Running."
            ),
            "inactiveVehicle": (
                "Offline on FuelArk (InActive). Battery-remove is counted separately, "
                "same as FuelArk Home."
            ),
            "identityCheck": (
                f"FuelArk: total {total_n} ≈ active {active_n} + inactive {inactive_n} "
                f"+ battery {battery_n}"
                f" · running {running_n} ⊂ active · source {kpi_source}"
            ),
        },
    }
