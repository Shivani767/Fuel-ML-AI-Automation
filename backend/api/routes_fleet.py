"""Fleet list + KPI endpoints."""

from __future__ import annotations

from flask import Blueprint, jsonify, request

from backend import config
from backend.services import data_store
from backend.services.fuelark_live import refresh_fleet_live_snapshot, snapshot_age_seconds

bp = Blueprint("fleet", __name__)


def _snapshot_meta() -> dict:
    """Age of the cached snapshot, and whether it can still be trusted.

    Active/Running are computed against wall-clock, so once the cache is older than
    ACTIVE_ONLINE_MINUTES every vehicle falls out of the window and reads inactive.
    Without this the UI cannot tell "all offline" from "data went stale".
    """
    age = snapshot_age_seconds()
    limit = config.ACTIVE_ONLINE_MINUTES * 60.0
    return {
        "ageSeconds": None if age is None else round(age),
        "ageMinutes": None if age is None else round(age / 60.0, 1),
        "stale": True if age is None else age > limit,
        "onlineWindowMinutes": config.ACTIVE_ONLINE_MINUTES,
    }


def _maybe_refresh_live():
    """Refresh today's FuelArk snapshot unless ?refresh=0."""
    flag = str(request.args.get("refresh", "1")).lower()
    if flag in ("0", "false", "no", "skip"):
        return {"skipped": True}
    force = flag in ("force", "2")
    try:
        hours = float(request.args.get("hours", "24"))
    except ValueError:
        hours = 24.0
    try:
        return refresh_fleet_live_snapshot(hours=hours, force=force)
    except Exception as e:
        return {"ok": False, "error": str(e)}


@bp.get("/api/fleet/summary")
def fleet_summary():
    data_store.ensure_loaded()
    live = _maybe_refresh_live()
    cards = data_store.fleet_device_cards(today_only=True)
    payload = data_store.fleet_summary(cards)
    payload["liveRefresh"] = live
    payload["snapshot"] = _snapshot_meta()
    return jsonify(payload)


@bp.get("/api/fleet/devices")
def fleet_devices():
    data_store.ensure_loaded()
    live = _maybe_refresh_live()
    q = (request.args.get("q") or request.args.get("search") or "").strip().lower()
    cards = data_store.fleet_device_cards(today_only=True)
    if q:
        cards = [
            c
            for c in cards
            if q in c["deviceName"].lower() or q in str(c.get("imei") or "").lower()
        ]
    # Attach refresh meta only via header-less list; clients ignore unknown fields on items
    return jsonify(cards)
