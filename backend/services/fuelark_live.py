"""Live FuelArk API fetch helpers used by the backend."""

from __future__ import annotations

import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from typing import Optional, Tuple

from backend import config
from backend.services import data_store
from backend.services.vehicle_aliases import to_display_name

_fleet_refresh_lock = threading.Lock()
_fleet_refresh_at = 0.0
_fleet_refresh_result = {"ok": False, "fetched": 0, "devices": 0, "error": None}
_bg_thread = None

# Delta-refresh tuning. The old refresh re-pulled the whole day from all 104 devices
# every cycle; these keep each call to the few minutes that are actually new.
OVERLAP_MINUTES = 10          # re-ask slightly before the newest held point
SILENT_AFTER_EMPTY_POLLS = 3  # consecutive empty polls before backing off
SILENT_RECHECK_EVERY_N = 6    # ...then poll such a device on 1 cycle in 6

# Consecutive empty responses per IMEI. Backing off must be driven by what polling
# actually returned, not by how old the in-memory data is: on a cold start every
# device's newest point comes from the historical dump, so an age-based rule
# silently skips the entire fleet and everything reads inactive.
_empty_polls: dict = {}
_cycle_no = 0


def _latest_point_by_imei() -> dict:
    """Newest timestamp held in memory, per IMEI, as naive datetimes.

    Points store `time` as an ISO *string*, so it is parsed here to be comparable
    with _ist_now(). ISO strings also sort correctly, so points[-1] is the newest.
    """
    out: dict = {}
    for points in data_store.device_data_dict.values():
        if not points:
            continue
        last = points[-1]
        imei = str(last.get("imei") or "")
        raw = last.get("time")
        if not imei or not raw:
            continue
        if isinstance(raw, datetime):
            ts = raw.replace(tzinfo=None) if raw.tzinfo else raw
        else:
            try:
                ts = datetime.fromisoformat(str(raw)[:26])
            except ValueError:
                continue
        cur = out.get(imei)
        if cur is None or ts > cur:
            out[imei] = ts
    return out


def snapshot_age_seconds() -> Optional[float]:
    """Seconds since the last successful live merge, or None if never refreshed.

    Fleet status is derived from wall-clock ("seen in the last 30 minutes"), so a
    stale cache silently reports every vehicle as inactive. Callers surface this
    so "0 active" can be told apart from "0 active because the data is an hour old".
    """
    if not _fleet_refresh_at:
        return None
    return max(0.0, time.time() - _fleet_refresh_at)


def start_background_refresher(interval_sec: float = 300.0, hours: float = 6.0):
    """Keep the fleet snapshot warm on a timer, independent of incoming requests.

    A live pull takes ~2 minutes and sometimes longer when FuelArk throttles, so it
    can never run inside a dashboard request. Refreshing on our own schedule lets
    the UI read cache exclusively and still see recent data.
    """
    global _bg_thread
    if _bg_thread is not None and _bg_thread.is_alive():
        return _bg_thread

    def _loop():
        while True:
            try:
                res = refresh_fleet_live_snapshot(hours=hours, force=True)
                print(
                    f"[fleet-bg] refreshed: {res.get('devices')} devices, "
                    f"{res.get('fetched')} pts"
                )
            except Exception as e:  # never let the thread die
                print(f"[fleet-bg] refresh failed: {e}")
            time.sleep(interval_sec)

    _bg_thread = threading.Thread(target=_loop, name="fleet-refresher", daemon=True)
    _bg_thread.start()
    print(f"[fleet-bg] background refresher started (every {interval_sec:.0f}s)")
    return _bg_thread


def _ensure_root():
    root = str(config.PROJECT_ROOT)
    if root not in sys.path:
        sys.path.insert(0, root)


def _ist_now() -> datetime:
    try:
        from zoneinfo import ZoneInfo

        return datetime.now(ZoneInfo("Asia/Kolkata")).replace(tzinfo=None)
    except Exception:
        return datetime.now()


def fleet_imei_list() -> list:
    """Unique IMEIs from map + fleet_imeis.txt."""
    data_store.ensure_loaded()
    imeis = set()
    for imei in data_store.device_imei_map.values():
        if imei:
            imeis.add(str(imei).strip())
    path = config.FLEET_IMEIS_PATH
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                imeis.add(line)
    return sorted(imeis)


def refresh_fleet_live_snapshot(
    hours: float = 24,
    workers: int = 10,
    min_interval_sec: float = 60,
    force: bool = False,
) -> dict:
    """Pull recent FuelArk points for all fleet IMEIs and merge into memory.

    Cached so dashboard polling (every ~20s) does not re-hit the API every time.
    """
    global _fleet_refresh_at, _fleet_refresh_result
    now_ts = time.time()
    with _fleet_refresh_lock:
        if (
            not force
            and _fleet_refresh_at
            and (now_ts - _fleet_refresh_at) < min_interval_sec
        ):
            return dict(_fleet_refresh_result)

    _ensure_root()
    from fuel_api import fetch_live_window, login_and_get_token
    from fetch_live_data import stamp_points

    imeis = fleet_imei_list()
    if not imeis:
        result = {"ok": False, "fetched": 0, "devices": 0, "error": "no IMEIs"}
        with _fleet_refresh_lock:
            _fleet_refresh_result = result
            _fleet_refresh_at = time.time()
        return result

    end = _ist_now()
    day_start = end.replace(hour=0, minute=0, second=0, microsecond=0)
    full_start = max(end - timedelta(hours=float(hours)), day_start)

    token = login_and_get_token()
    all_parsed = []
    errors = 0
    skipped = 0
    windows = []

    latest = _latest_point_by_imei()
    global _cycle_no
    _cycle_no += 1

    def _plan(imei: str):
        """Smallest window that still catches everything new for this device."""
        key = str(imei)
        # Back off only for devices that have actually answered with nothing,
        # several times running, and even then keep re-checking periodically.
        if _empty_polls.get(key, 0) >= SILENT_AFTER_EMPTY_POLLS:
            if (_cycle_no + hash(key)) % SILENT_RECHECK_EVERY_N != 0:
                return None
            return full_start

        last = latest.get(key)
        if last is None or last < full_start:
            return full_start          # nothing recent held -> take the full window
        # Overlap slightly so a point landing mid-request is not missed.
        return max(last - timedelta(minutes=OVERLAP_MINUTES), full_start)

    def _one(imei: str):
        key = str(imei)
        start_i = _plan(imei)
        if start_i is None:
            return [], None, True
        try:
            raw = fetch_live_window(imei, start_i, end)
            if raw:
                _empty_polls[key] = 0
            else:
                _empty_polls[key] = _empty_polls.get(key, 0) + 1
            stamped = stamp_points(raw or [], imei)
            return data_store.parse_data(stamped), None, False
        except Exception as e:
            return [], str(e), False

    with ThreadPoolExecutor(max_workers=max(2, int(workers))) as ex:
        futs = {ex.submit(_one, imei): imei for imei in imeis}
        for fut in as_completed(futs):
            pts, err, was_skipped = fut.result()
            if was_skipped:
                skipped += 1
            if err:
                errors += 1
            if pts:
                all_parsed.extend(pts)
    windows.append(skipped)

    added = 0
    if all_parsed:
        added = data_store.merge_live_points(all_parsed)

    # Ensure every responding IMEI is in the name map under display name
    by_imei = {}
    for p in all_parsed:
        imei = str(p.get("imei") or "")
        name = str(p.get("deviceName") or "").strip()
        if imei and name:
            by_imei[imei] = name
    if by_imei:
        for imei, name in by_imei.items():
            data_store.device_imei_map[name] = imei
        data_store.persist_imei_map()

    result = {
        "ok": True,
        "fetched": len(all_parsed),
        "merged": added,
        "devices": len(by_imei),
        "imeis": len(imeis),
        "errors": errors,
        "from": full_start.isoformat(timespec="seconds"),
        "to": end.isoformat(timespec="seconds"),
        "skippedSilent": skipped,
        "error": None,
    }
    with _fleet_refresh_lock:
        _fleet_refresh_result = result
        _fleet_refresh_at = time.time()
    print(
        f"[fleet] live snapshot: {result['devices']}/{result['imeis']} devices, "
        f"{result['fetched']} pts (merged {added})",
        flush=True,
    )
    return result


def add_imei_to_fleet(imei: str, days: int = 3) -> dict:
    """Fetch one IMEI from FuelArk, merge into memory, persist map + fleet list."""
    imei = str(imei or "").strip()
    if not imei.isdigit() or len(imei) < 14 or len(imei) > 16:
        return {
            "ok": False,
            "error": "IMEI must be 14–16 digits",
            "imei": imei,
        }

    _ensure_root()
    from fuel_api import fetch_imei_dates, login_and_get_token
    from fetch_live_data import stamp_points, save_live_data, LIVE_DATA_PATH

    data_store.ensure_loaded()
    days = max(1, min(int(days or 3), 14))
    end = _ist_now()
    start = end - timedelta(days=days)

    token = login_and_get_token()
    try:
        raw = fetch_imei_dates(imei, start, end, token=token) or []
    except Exception as exc:
        return {"ok": False, "error": str(exc), "imei": imei}

    stamped = stamp_points(raw, imei)
    parsed = data_store.parse_data(stamped) if stamped else []

    names: dict = {}
    for p in stamped:
        n = str(p.get("deviceName") or "").strip()
        if n:
            names[n] = names.get(n, 0) + 1
    device_name = max(names, key=names.get) if names else f"IMEI-{imei}"
    device_name = to_display_name(device_name, imei)

    # Drop any stale name→imei for this IMEI, then register
    stale = [n for n, i in list(data_store.device_imei_map.items()) if str(i) == imei]
    for n in stale:
        if n != device_name:
            data_store.device_imei_map.pop(n, None)
    data_store.device_imei_map[device_name] = imei
    data_store.persist_imei_map()

    # Persist fleet list
    path = config.FLEET_IMEIS_PATH
    lines = []
    if path.exists():
        lines = [
            ln.strip()
            for ln in path.read_text(encoding="utf-8").splitlines()
            if ln.strip() and not ln.strip().startswith("#")
        ]
    if imei not in lines:
        lines.append(imei)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(sorted(set(lines))) + "\n", encoding="utf-8")

    added = 0
    if parsed:
        added = data_store.merge_live_points(parsed)
        # Append to live_fuel_data.json (best-effort)
        try:
            import json
            from pathlib import Path

            live_path = Path(LIVE_DATA_PATH)
            existing = []
            if live_path.exists():
                existing = json.loads(live_path.read_text(encoding="utf-8"))
            if not isinstance(existing, list):
                existing = []
            seen = {
                (
                    str(p.get("deviceDataInsertTime") or p.get("time") or ""),
                    str(p.get("imei") or ""),
                )
                for p in existing
            }
            for p in stamped:
                key = (
                    str(p.get("deviceDataInsertTime") or p.get("time") or ""),
                    imei,
                )
                if key not in seen:
                    existing.append(p)
                    seen.add(key)
            save_live_data(existing)
        except Exception as exc:
            print(f"[imei] live file save skipped: {exc}", flush=True)

    latest = None
    series = data_store.device_data_dict.get(device_name) or []
    if series:
        latest = {
            "time": series[-1].get("time"),
            "fuel": series[-1].get("fuelInLiter"),
        }

    return {
        "ok": True,
        "imei": imei,
        "deviceName": device_name,
        "pointsFetched": len(stamped),
        "pointsMerged": added,
        "from": start.isoformat(timespec="seconds"),
        "to": end.isoformat(timespec="seconds"),
        "latest": latest,
        "mapped": len(data_store.device_imei_map),
        "error": None if stamped else "No telemetry returned for this IMEI in the lookback window",
    }


def build_imei_map_from_api(lookback_hours: float = 6) -> dict:
    _ensure_root()
    from fetch_100_vehicles import IMEIS
    from fuel_api import fetch_imei_range, login_and_get_token
    from fetch_live_data import stamp_points, update_device_imei_map

    token = login_and_get_token()
    lookback_days = max(1, int(lookback_hours / 24) + 1)
    discovered = {}

    def _fetch(imei):
        points = fetch_imei_range(imei, lookback_days=lookback_days, token=token)
        return stamp_points(points, imei)

    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = {executor.submit(_fetch, imei): imei for imei in IMEIS}
        for future in as_completed(futures):
            try:
                points = future.result()
            except Exception:
                continue
            for point in points:
                imei = point.get("imei")
                name = to_display_name(str(point.get("deviceName") or "").strip(), imei)
                if name and imei:
                    discovered[name] = str(imei)

    if discovered:
        data_store.device_imei_map.update(discovered)
        data_store.persist_imei_map()
        update_device_imei_map(
            [{"deviceName": n, "imei": i} for n, i in discovered.items()]
        )
    return discovered


def fetch_live_for_device(
    device_name: str, hours: float = 2, auto_resolve: bool = False
) -> Tuple[Optional[list], Optional[str]]:
    end = _ist_now()
    start = end - timedelta(hours=float(hours))
    return fetch_range_for_device(device_name, start, end, auto_resolve=auto_resolve)


def fetch_range_for_device(
    device_name: str,
    start: datetime,
    end: datetime,
    auto_resolve: bool = False,
) -> Tuple[Optional[list], Optional[str]]:
    """Pull FuelArk telemetry for an arbitrary date range and merge into memory."""
    _ensure_root()
    from fuel_api import fetch_imei_dates, login_and_get_token
    from fetch_live_data import stamp_points

    data_store.ensure_loaded()
    resolved = data_store.resolve_device_name(device_name) or device_name
    imei = data_store.resolve_imei(resolved)
    if not imei and auto_resolve:
        discovered = build_imei_map_from_api(lookback_hours=48)
        imei = discovered.get(resolved) or data_store.resolve_imei(resolved)
        if not imei:
            want = resolved.strip().lower()
            for n, i in discovered.items():
                if n.strip().lower() == want:
                    imei = i
                    break
    if not imei:
        return None, (
            "IMEI not mapped for this device. "
            "Call /api/live/resolve-map once, then retry."
        )

    if end < start:
        start, end = end, start

    if (end.date() - start.date()).days > 31:
        start = end - timedelta(days=31)

    token = login_and_get_token()
    raw_points = fetch_imei_dates(imei, start, end, token=token)
    stamped = stamp_points(raw_points, imei)
    parsed = data_store.parse_data(stamped)

    window = []
    for entry in parsed:
        try:
            ts = datetime.fromisoformat(entry["time"])
        except Exception:
            window.append(entry)
            continue
        if ts < start or ts > end:
            continue
        window.append(entry)

    data_store.merge_live_points(window if window else parsed)
    return window, None
