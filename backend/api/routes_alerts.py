"""Fill/theft alert digest endpoints."""

from __future__ import annotations

import json

from flask import Blueprint, jsonify, request, send_file

from backend.services import alerts, voltage_profile

bp = Blueprint("alerts", __name__)


@bp.get("/api/alerts/latest")
def alerts_latest():
    """Most recent 30-minute digest, or an empty shell before the first run."""
    digest = alerts.latest_digest()
    if digest is None:
        return jsonify({
            "generatedAt": None,
            "pending": True,
            "message": "No alert cycle has completed yet.",
            "theftCount": 0, "fillCount": 0, "suppressedCount": 0,
            "byVehicle": [], "thefts": [], "fills": [], "suppressed": [],
        })
    return jsonify(digest)


@bp.get("/api/alerts")
def alerts_history():
    """Alerts from the day-wise CSVs, newest first.

    `day=YYYY-MM-DD` reads one day; omit it to span every day on disk.
    `filtered=1` also returns events the confirmation gate suppressed.
    `since` filters on eventTime; `limit` caps the response.
    """
    day = (request.args.get("day") or "").strip() or None
    since = (request.args.get("since") or "").strip()
    include_filtered = str(request.args.get("filtered", "0")).lower() in ("1", "true", "yes")
    try:
        limit = int(request.args.get("limit", "200"))
    except ValueError:
        limit = 200

    rows = alerts.read_daily_alerts(day=day, include_filtered=include_filtered)
    if since:
        rows = [r for r in rows if str(r.get("eventTime", "")) >= since]
    rows.sort(key=lambda r: str(r.get("eventTime", "")), reverse=True)
    return jsonify({
        "count": len(rows),
        "day": day,
        "availableDays": alerts.available_days(),
        "alerts": rows[:limit],
    })


@bp.get("/api/alerts/days")
def alerts_days():
    """Per-day totals, for a calendar or a day picker."""
    out = []
    for day in alerts.available_days():
        rows = alerts.read_daily_alerts(day=day, include_filtered=True)
        released = [r for r in rows if r.get("status") == "released"]
        thefts = [r for r in released if "theft" in str(r.get("eventType", "")).lower()]
        fills = [r for r in released if r not in thefts]

        def total(rs):
            s = 0.0
            for r in rs:
                try:
                    s += float(r.get("volume_L") or 0)
                except (TypeError, ValueError):
                    pass
            return round(s, 2)

        out.append({
            "day": day,
            "theftCount": len(thefts), "theftLiters": total(thefts),
            "fillCount": len(fills), "fillLiters": total(fills),
            "filteredCount": sum(1 for r in rows if r.get("status") == "filtered"),
            "vehicles": len({r.get("deviceName") for r in released}),
        })
    return jsonify({"count": len(out), "days": out})


@bp.get("/api/alerts/download/<day>")
def alerts_download(day: str):
    """Raw CSV for one day, for spreadsheets."""
    kind = (request.args.get("kind") or "alerts").lower()
    path = (alerts.filtered_csv_for(day) if kind == "filtered"
            else alerts.alerts_csv_for(day))
    if not path.exists():
        return jsonify({"error": f"no {kind} csv for {day}"}), 404
    return send_file(path, mimetype="text/csv", as_attachment=True,
                     download_name=path.name)


@bp.post("/api/alerts/run")
def alerts_run_now():
    """Force a cycle immediately instead of waiting for the timer."""
    try:
        return jsonify(alerts.run_cycle())
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@bp.get("/api/alerts/voltage-profiles")
def voltage_profiles():
    """Per-device voltage coupling, and which sensors need recalibration.

    Surfaced because a large swing is a hardware problem, not just a filter input:
    those devices cannot report small thefts at all until they are fixed.
    """
    profiles = voltage_profile.load_profiles()
    return jsonify({
        "count": len(profiles),
        "coupled": sum(1 for p in profiles.values() if p.get("coupled")),
        "needsRecalibration": voltage_profile.needs_recalibration(),
        "profiles": sorted(profiles.values(), key=lambda p: -p.get("swing_liters", 0)),
    })
