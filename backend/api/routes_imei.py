"""Add / list IMEIs from the UI (persisted for lifetime in device_imei_map)."""

from __future__ import annotations

from flask import Blueprint, jsonify, request

from backend.services import data_store
from backend.services.fuelark_live import add_imei_to_fleet

bp = Blueprint("imei", __name__)


@bp.get("/api/imei/list")
def list_imeis():
    """Existing IMEI roster exactly as stored (name → IMEI), plus live point stats."""
    data_store.ensure_loaded()
    rows = []
    seen_imeis = set()
    for name, imei in sorted(data_store.device_imei_map.items(), key=lambda x: x[0].lower()):
        imei_s = str(imei or "").strip()
        if not imei_s:
            continue
        seen_imeis.add(imei_s)
        series = data_store.device_data_dict.get(name) or []
        latest = series[-1] if series else None
        rows.append(
            {
                "deviceName": name,
                "imei": imei_s,
                "points": len(series),
                "latestTime": latest.get("time") if latest else None,
                "latestFuel": latest.get("fuelInLiter") if latest else None,
                "inMap": True,
            }
        )
    for name, series in data_store.device_data_dict.items():
        if name in data_store.device_imei_map:
            continue
        imei_s = ""
        if series:
            imei_s = str(series[-1].get("imei") or "").strip()
        if imei_s and imei_s in seen_imeis:
            continue
        latest = series[-1] if series else None
        rows.append(
            {
                "deviceName": name,
                "imei": imei_s or None,
                "points": len(series),
                "latestTime": latest.get("time") if latest else None,
                "latestFuel": latest.get("fuelInLiter") if latest else None,
                "inMap": False,
            }
        )
    rows.sort(key=lambda r: (r.get("deviceName") or "").lower())
    return jsonify(
        {
            "ok": True,
            "count": len(rows),
            "mapped": len(data_store.device_imei_map),
            "devices": rows,
        }
    )


@bp.route("/api/imei/add", methods=["POST", "OPTIONS"])
def add_imei():
    if request.method == "OPTIONS":
        return ("", 204)

    body = request.get_json(silent=True) or {}
    imei = str(body.get("imei") or request.args.get("imei") or "").strip()
    lifetime = str(body.get("lifetime") or request.args.get("lifetime") or "1").lower() in (
        "1",
        "true",
        "yes",
        "lifetime",
    )
    try:
        days = int(body.get("days") or request.args.get("days") or (30 if lifetime else 3))
    except (TypeError, ValueError):
        days = 30 if lifetime else 3
    days = max(1, min(days, 60))

    result = add_imei_to_fleet(imei, days=days)
    if result.get("ok"):
        result["lifetime"] = True
    status = 200 if result.get("ok") else 400
    return jsonify(result), status
