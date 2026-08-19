"""Device series + Fuelark summary endpoints."""

from __future__ import annotations

from datetime import datetime

from flask import Blueprint, jsonify, request

from backend import config
from backend.services import data_store
from backend.services.detection import detect_fill_theft
from backend.services.fuelark_live import fetch_range_for_device
from backend.services.metrics import compute_device_summary, series_point
from backend.services.session_ml import daily_anomalies_for_imei, load_model
from backend.services.slabs import aggregate_to_slabs, downsample

bp = Blueprint("devices", __name__)


def _date_range_from_request():
    from_date = data_store.parse_date_param(request.args.get("from"))
    to_date = data_store.parse_date_param(request.args.get("to"))
    if to_date and from_date and to_date < from_date:
        from_date, to_date = to_date, from_date
    if to_date and to_date.hour == 0 and to_date.minute == 0 and to_date.second == 0:
        to_date = to_date.replace(hour=23, minute=59, second=59, microsecond=999999)
    return from_date, to_date


def _slab_param():
    raw = request.args.get("slab", request.args.get("slab_minutes", ""))
    if str(raw).lower() in ("", "0", "raw", "false", "none"):
        return None
    try:
        return max(1, int(raw))
    except (TypeError, ValueError):
        return None


@bp.get("/api/devices")
@bp.get("/api/device-names")
def list_device_names():
    data_store.ensure_loaded()
    mapped = [n for n in data_store.device_imei_map if n in data_store.device_data_dict]
    others = [n for n in data_store.device_data_dict if n not in data_store.device_imei_map]
    return jsonify(mapped + others)


# Legacy alias used by old templates
@bp.get("/api/devices-legacy-alias")
def _unused():
    return jsonify([])


@bp.get("/api/device/<path:device_name>")
def device_points_legacy(device_name):
    """Legacy points endpoint (optional slab). Prefer /series for multi-field chart."""
    data_store.ensure_loaded()
    resolved = data_store.resolve_device_name(device_name)
    if not resolved:
        return jsonify([])
    from_date, to_date = _date_range_from_request()
    points = data_store.apply_date_filter(
        data_store.device_data_dict[resolved], from_date, to_date
    )
    slab = _slab_param()
    if slab is None and request.args.get("slab", "30") not in ("0", "raw", "false", "none", ""):
        if "slab" not in request.args:
            slab = 30
        else:
            try:
                slab = int(request.args.get("slab", 30))
            except ValueError:
                slab = 30
    if slab:
        points = aggregate_to_slabs(points, slab_minutes=slab)
    return jsonify(points)


def _maybe_fetch_range(resolved: str, from_date, to_date, local_count: int = 0):
    """Pull FuelArk for the selected window.

    fetch=auto (default): only if local window is empty
    fetch=1|force: always refresh from API
    fetch=0: never
    """
    mode = str(request.args.get("fetch", "auto")).lower()
    if mode in ("0", "false", "none", "no"):
        return 0, None
    if mode in ("auto", "") and local_count > 0:
        return 0, None
    if not from_date or not to_date:
        return 0, None
    try:
        recent, fetch_error = fetch_range_for_device(
            resolved, from_date, to_date, auto_resolve=True
        )
        return len(recent or []), fetch_error
    except Exception as exc:
        return 0, str(exc)


@bp.get("/api/devices/<path:device_name>/summary")
@bp.get("/api/device/<path:device_name>/summary")
def device_summary(device_name):
    data_store.ensure_loaded()
    resolved = data_store.resolve_device_name(device_name)
    from_date, to_date = _date_range_from_request()
    if not resolved and from_date and to_date:
        # Try fetch first — may discover name via IMEI map
        try:
            fetch_range_for_device(device_name, from_date, to_date, auto_resolve=True)
        except Exception:
            pass
        resolved = data_store.resolve_device_name(device_name)
    if not resolved:
        return jsonify({})
    _maybe_fetch_range(
        resolved,
        from_date,
        to_date,
        local_count=len(
            data_store.apply_date_filter(
                data_store.device_data_dict.get(resolved, []), from_date, to_date
            )
        ),
    )
    resolved = data_store.resolve_device_name(resolved) or resolved
    data = data_store.apply_date_filter(
        data_store.device_data_dict.get(resolved, []), from_date, to_date
    )
    if not data:
        return jsonify({})
    # detect=0 → fast KPI cards (fuel/ignition/speed) without running session detector
    detect_mode = str(request.args.get("detect", "1")).lower()
    if detect_mode in ("0", "false", "no", "skip"):
        return jsonify(compute_device_summary(resolved, data, events=[]))
    # Use session detector (+ RF) for fill/theft KPIs — not noisy fuel_events.json
    try:
        theft_min = float(request.args.get("theft_min", "5"))
        fill_min = float(request.args.get("fill_min", "5"))
    except ValueError:
        theft_min, fill_min = 5.0, 5.0
    events = detect_fill_theft(
        resolved, data, theft_min=theft_min, fill_min=fill_min
    )
    return jsonify(compute_device_summary(resolved, data, events=events))


@bp.get("/api/devices/<path:device_name>/series")
def device_series(device_name):
    """Multi-series points for Live Chart / Live Table (Fuelark parameters)."""
    data_store.ensure_loaded()
    from_date, to_date = _date_range_from_request()
    resolved = data_store.resolve_device_name(device_name)
    if not resolved:
        # Still attempt API fetch using whatever name/imei mapping we have
        if from_date and to_date:
            try:
                fetch_range_for_device(device_name, from_date, to_date, auto_resolve=True)
            except Exception:
                pass
            resolved = data_store.resolve_device_name(device_name)

    fetched_count, fetch_error = (0, None)
    if resolved and from_date and to_date:
        local = data_store.apply_date_filter(
            data_store.device_data_dict.get(resolved, []), from_date, to_date
        )
        fetched_count, fetch_error = _maybe_fetch_range(
            resolved, from_date, to_date, local_count=len(local)
        )
        resolved = data_store.resolve_device_name(resolved) or resolved

    if not resolved:
        return jsonify(
            {
                "deviceName": device_name,
                "points": [],
                "count": 0,
                "error": fetch_error or "Device not found",
            }
        )

    points = data_store.apply_date_filter(
        data_store.device_data_dict.get(resolved, []), from_date, to_date
    )

    slab = _slab_param()
    if slab:
        points = aggregate_to_slabs(points, slab_minutes=slab)
    else:
        every = request.args.get("downsample", str(config.DEFAULT_DOWNSAMPLE))
        try:
            every_n = max(1, int(every))
        except ValueError:
            every_n = config.DEFAULT_DOWNSAMPLE
        max_pts = request.args.get("limit", str(config.MAX_SERIES_POINTS))
        try:
            max_pts = max(100, int(max_pts))
        except ValueError:
            max_pts = config.MAX_SERIES_POINTS
        points = downsample(points, every_n=every_n, max_points=max_pts)

    series = [series_point(p) for p in points]
    return jsonify(
        {
            "deviceName": resolved,
            "imei": data_store.resolve_imei(resolved),
            "count": len(series),
            "slabMinutes": slab or 0,
            "from": from_date.isoformat() if from_date else None,
            "to": to_date.isoformat() if to_date else None,
            "fetchedCount": fetched_count,
            "error": fetch_error,
            "points": series,
        }
    )


@bp.get("/api/devices/<path:device_name>/events")
@bp.get("/api/device/<path:device_name>/events")
def device_fill_theft_events(device_name):
    """Detect Fuel Filling / Fuel Theft for any from–to window (May, June, July, …)."""
    data_store.ensure_loaded()
    from_date, to_date = _date_range_from_request()
    resolved = data_store.resolve_device_name(device_name)
    if not resolved and from_date and to_date:
        try:
            fetch_range_for_device(device_name, from_date, to_date, auto_resolve=True)
        except Exception:
            pass
        resolved = data_store.resolve_device_name(device_name)
    if not resolved:
        return jsonify(
            {
                "deviceName": device_name,
                "fills": [],
                "thefts": [],
                "events": [],
                "error": "Device not found",
            }
        )

    local = data_store.apply_date_filter(
        data_store.device_data_dict.get(resolved, []), from_date, to_date
    )
    _maybe_fetch_range(resolved, from_date, to_date, local_count=len(local))
    resolved = data_store.resolve_device_name(resolved) or resolved
    points = data_store.apply_date_filter(
        data_store.device_data_dict.get(resolved, []), from_date, to_date
    )
    # Do NOT stride-downsample here — that invents fake fuel jumps.
    # detect_fill_theft() already processes multi-day ranges day-by-day.

    try:
        min_liters = float(request.args.get("min_liters", "5"))
    except ValueError:
        min_liters = 5.0
    try:
        theft_min = float(request.args.get("theft_min", str(max(min_liters, 5))))
    except ValueError:
        theft_min = 5.0
    try:
        fill_min = float(request.args.get("fill_min", "5"))
    except ValueError:
        fill_min = 5.0

    include_noise = str(request.args.get("include_stored", "0")).lower() in (
        "1",
        "true",
        "yes",
    )

    # Warm session RF once so first request pays load cost
    load_model()
    events = detect_fill_theft(
        resolved,
        points,
        theft_min=theft_min,
        fill_min=fill_min,
        include_stored_noise=include_noise,
    )
    fills = [e for e in events if e.get("eventType") == "Fuel Filling"]
    thefts = [e for e in events if e.get("eventType") == "Fuel Theft"]
    imei = data_store.resolve_imei(resolved) or ""
    daily = daily_anomalies_for_imei(imei, from_date, to_date)
    return jsonify(
        {
            "deviceName": resolved,
            "imei": imei or None,
            "from": from_date.isoformat() if from_date else None,
            "to": to_date.isoformat() if to_date else None,
            "pointCount": len(points),
            "fillCount": len(fills),
            "theftCount": len(thefts),
            "totalFillLiters": round(sum(float(e.get("volume") or 0) for e in fills), 2),
            "totalTheftLiters": round(sum(float(e.get("volume") or 0) for e in thefts), 2),
            "fills": fills,
            "thefts": thefts,
            "events": events,
            "dailyAnomalies": daily,
            "mlEnabled": load_model() is not None,
        }
    )


@bp.get("/api/events")
def get_events():
    from_date, to_date = _date_range_from_request()
    events = data_store.load_events()
    return jsonify(
        data_store.apply_date_filter(events, from_date, to_date, time_key="eventTime")
    )


@bp.get("/api/ml/status")
def ml_status():
    """Whether session RF + daily anomaly artifacts are available."""
    bundle = load_model()
    anom = (config.PROJECT_ROOT / "models" / "june_daily_residual_anomalies.csv").exists()
    return jsonify(
        {
            "sessionRf": bundle is not None,
            "features": list((bundle or {}).get("features") or []),
            "dailyAnomalies": anom,
            "modelPath": str(config.PROJECT_ROOT / "models" / "fuel_session_rf.pkl"),
        }
    )


@bp.get("/api/ml_alerts")
def get_ml_alerts():
    alerts = data_store.load_ml_alerts()
    from_date, to_date = _date_range_from_request()
    device_name = request.args.get("device")
    if device_name:
        alerts = [a for a in alerts if a.get("deviceName") == device_name]
    return jsonify(
        data_store.apply_date_filter(alerts, from_date, to_date, time_key="eventTime")
    )
