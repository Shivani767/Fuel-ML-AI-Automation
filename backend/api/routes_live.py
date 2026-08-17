"""Live polling + SSE (multi-series points)."""

from __future__ import annotations

import json
import time
from datetime import datetime, timedelta

from flask import Blueprint, Response, jsonify, request

from backend import config
from backend.services import data_store
from backend.services.fuelark_live import build_imei_map_from_api, fetch_live_for_device
from backend.services.metrics import series_point
from backend.services.slabs import aggregate_to_slabs

bp = Blueprint("live", __name__)


def _use_slab():
    raw = str(request.args.get("slab", "0"))
    if raw.lower() in ("0", "raw", "false", "none", ""):
        return None
    try:
        return max(1, int(raw))
    except ValueError:
        return None


@bp.get("/api/live/devices")
def live_devices():
    data_store.ensure_loaded()
    devices = []
    for name, imei in sorted(data_store.device_imei_map.items()):
        series = data_store.device_data_dict.get(name, [])
        latest = series[-1] if series else None
        devices.append(
            {
                "deviceName": name,
                "imei": imei,
                "points": len(series),
                "latestTime": latest["time"] if latest else None,
                "latestFuel": latest["fuelInLiter"] if latest else None,
            }
        )
    return jsonify(devices)


@bp.get("/api/live/status")
def live_status():
    data_store.ensure_loaded()
    return jsonify(
        {
            "devicesMapped": len(data_store.device_imei_map),
            "deviceCount": len(data_store.device_data_dict),
            "mapPath": str(config.DEVICE_IMEI_MAP_PATH),
        }
    )


@bp.route("/api/live/resolve-map", methods=["POST", "GET"])
def live_resolve_map():
    hours = float(request.args.get("hours", "6"))
    try:
        discovered = build_imei_map_from_api(lookback_hours=hours)
        return jsonify(
            {
                "ok": True,
                "mapped": len(data_store.device_imei_map),
                "discovered": len(discovered),
                "devices": sorted(data_store.device_imei_map.keys()),
            }
        )
    except Exception as exc:
        return (
            jsonify(
                {
                    "ok": False,
                    "error": str(exc),
                    "mapped": len(data_store.device_imei_map),
                }
            ),
            500,
        )


@bp.get("/api/live/<path:device_name>")
def live_device_json(device_name):
    since = request.args.get("since")
    hours = request.args.get("hours", "6")
    do_fetch = request.args.get("fetch", "1") == "1"
    limit = int(request.args.get("limit", "500"))
    slab = _use_slab()

    fetch_error = None
    fetched_count = 0
    if do_fetch:
        try:
            recent, fetch_error = fetch_live_for_device(device_name, hours=float(hours))
            fetched_count = len(recent or [])
        except Exception as exc:
            fetch_error = str(exc)

    data_store.ensure_loaded()
    series = data_store.device_data_dict.get(device_name, [])
    if since:
        points = [p for p in series if p["time"] > since]
    else:
        try:
            cutoff = datetime.now() - timedelta(hours=float(hours))
            points = [p for p in series if datetime.fromisoformat(p["time"]) >= cutoff]
        except Exception:
            points = []
        if not points:
            points = series[-limit:]

    if slab:
        points = aggregate_to_slabs(points, slab_minutes=slab)
    points = points[-limit:]
    out = [series_point(p) for p in points]
    latest = out[-1] if out else None

    return jsonify(
        {
            "deviceName": device_name,
            "imei": data_store.resolve_imei(device_name),
            "since": since,
            "fetched": do_fetch,
            "fetchedCount": fetched_count,
            "newCount": len(out),
            "points": out,
            "slabMinutes": slab or 0,
            "latestTime": latest["time"] if latest else None,
            "latestFuel": latest["fuelLevel"] if latest else None,
            "latestIgnition": latest["ignition"] if latest else None,
            "error": fetch_error,
            "serverTime": datetime.now().isoformat(timespec="seconds"),
        }
    )


@bp.get("/api/live/<path:device_name>/sse")
def live_device_sse(device_name):
    hours = float(request.args.get("hours", "48"))
    interval_ms = int(request.args.get("interval_ms", "150"))
    fetch_every = int(request.args.get("fetch_every", "10"))
    limit = int(request.args.get("limit", "800"))
    slab = _use_slab()
    from_date = data_store.parse_date_param(request.args.get("from"))
    to_date = data_store.parse_date_param(request.args.get("to"))
    live_tail = request.args.get("live", "1") == "1"
    since_arg = request.args.get("since")

    def _series_for(name):
        resolved = data_store.resolve_device_name(name) or name
        return resolved, data_store.device_data_dict.get(resolved, [])

    def _window_points(series, since=None):
        pts = series
        if from_date or to_date:
            pts = data_store.apply_date_filter(pts, from_date, to_date)
        if since:
            pts = [p for p in pts if p["time"] > since]
        elif not from_date and not to_date:
            try:
                cutoff = datetime.now() - timedelta(hours=hours)
                pts = [p for p in series if datetime.fromisoformat(p["time"]) >= cutoff]
            except Exception:
                pts = []
            if not pts:
                pts = series[-max(limit * 20, 500) :]
        if slab:
            pts = aggregate_to_slabs(pts, slab_minutes=slab)
        return pts[-limit:]

    def generate():
        resolved = data_store.resolve_device_name(device_name) or device_name
        try:
            if from_date and to_date:
                from backend.services.fuelark_live import fetch_range_for_device

                fetch_range_for_device(resolved, from_date, to_date, auto_resolve=True)
            else:
                fetch_live_for_device(resolved, hours=max(hours, 6), auto_resolve=True)
            resolved = data_store.resolve_device_name(resolved) or resolved
        except Exception as exc:
            yield f"event: status\ndata: {json.dumps({'status': 'warn', 'message': str(exc)})}\n\n"

        data_store.ensure_loaded()
        resolved, series = _series_for(resolved)
        window = _window_points(series)
        if since_arg:
            window = [p for p in window if p["time"] > since_arg]
        yield (
            "event: status\ndata: "
            + json.dumps(
                {
                    "status": "streaming",
                    "deviceName": resolved,
                    "queued": len(window),
                    "slabMinutes": slab or 0,
                    "live": live_tail,
                }
            )
            + "\n\n"
        )

        last_time = since_arg
        emitted = 0
        for point in window:
            yield f"event: point\ndata: {json.dumps(series_point(point))}\n\n"
            last_time = point["time"]
            emitted += 1
            time.sleep(max(interval_ms, 10) / 1000.0)

            if emitted % fetch_every == 0:
                try:
                    if live_tail:
                        fetch_live_for_device(resolved, hours=max(hours, 6))
                    resolved, series = _series_for(resolved)
                    extras = _window_points(series, since=last_time)
                    for extra in extras:
                        yield f"event: point\ndata: {json.dumps(series_point(extra))}\n\n"
                        last_time = extra["time"]
                        time.sleep(max(interval_ms, 10) / 1000.0)
                except Exception:
                    pass

        if not live_tail:
            yield f"event: status\ndata: {json.dumps({'status': 'complete', 'latestTime': last_time})}\n\n"
            return

        while True:
            try:
                _, err = fetch_live_for_device(resolved, hours=max(hours, 1))
                if err:
                    yield f"event: status\ndata: {json.dumps({'status': 'waiting', 'message': err})}\n\n"
                # Extend "to" window to now for live appends
                resolved, series = _series_for(resolved)
                extras = _window_points(series, since=last_time)
                # Also include brand-new points past original to_date
                if last_time:
                    fresh = [
                        p
                        for p in series
                        if p["time"] > last_time
                        and (not from_date or datetime.fromisoformat(p["time"]) >= from_date)
                    ]
                    if slab:
                        fresh = aggregate_to_slabs(fresh, slab_minutes=slab)
                    # merge unique
                    seen = {e["time"] for e in extras}
                    for p in fresh:
                        if p["time"] not in seen:
                            extras.append(p)
                    extras.sort(key=lambda x: x["time"])
                for extra in extras:
                    yield f"event: point\ndata: {json.dumps(series_point(extra))}\n\n"
                    last_time = extra["time"]
                    time.sleep(max(interval_ms, 10) / 1000.0)
                if not extras:
                    yield (
                        "event: heartbeat\ndata: "
                        + json.dumps(
                            {
                                "serverTime": datetime.now().isoformat(timespec="seconds"),
                                "latestTime": last_time,
                                "slabMinutes": slab or 0,
                            }
                        )
                        + "\n\n"
                    )
                    time.sleep(15)
            except GeneratorExit:
                break
            except Exception as exc:
                yield f"event: status\ndata: {json.dumps({'status': 'error', 'message': str(exc)})}\n\n"
                time.sleep(5)

    return Response(
        generate(),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )
