"""Flask backend entry — Fuelark-style API for the React frontend."""

from __future__ import annotations

import os
import sys
from pathlib import Path

# Allow `python backend/app.py` and `python -m backend.app`
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from flask import Flask, jsonify, send_from_directory

from backend import config
from backend.api.routes_alerts import bp as alerts_bp
from backend.api.routes_devices import bp as devices_bp
from backend.api.routes_fleet import bp as fleet_bp
from backend.api.routes_imei import bp as imei_bp
from backend.api.routes_live import bp as live_bp
from backend.services import data_store


def create_app() -> Flask:
    app = Flask(__name__)

    @app.after_request
    def add_cors(response):
        response.headers["Access-Control-Allow-Origin"] = "*"
        response.headers["Access-Control-Allow-Headers"] = "Content-Type,Authorization"
        response.headers["Access-Control-Allow-Methods"] = "GET,POST,OPTIONS"
        return response

    @app.route("/api/health")
    def health():
        data_store.ensure_loaded()
        return jsonify(
            {
                "ok": True,
                "devices": len(data_store.device_data_dict),
                "mapped": len(data_store.device_imei_map),
            }
        )

    app.register_blueprint(fleet_bp)
    app.register_blueprint(devices_bp)
    app.register_blueprint(imei_bp)
    app.register_blueprint(live_bp)
    app.register_blueprint(alerts_bp)

    # Optional: serve built frontend from frontend/dist
    dist = ROOT / "frontend" / "dist"

    @app.route("/", defaults={"path": ""})
    @app.route("/<path:path>")
    def spa(path: str):
        if path.startswith("api/"):
            return jsonify({"error": "not found"}), 404
        if dist.exists():
            target = dist / path
            if path and target.exists() and target.is_file():
                return send_from_directory(dist, path)
            return send_from_directory(dist, "index.html")
        return jsonify(
            {
                "message": "Fuelark backend running",
                "frontend": "cd frontend && npm install && npm run dev",
                "health": "/api/health",
                "fleet": "/api/fleet/devices",
            }
        )

    return app


app = create_app()


def main():
    print("Loading telemetry into memory (may take a minute for June dump)...")
    data_store.ensure_loaded()
    try:
        from backend.services.session_ml import load_model

        if load_model():
            print("Session RF model ready (fuel_session_rf.pkl)")
        else:
            print("Session RF model not loaded — rule detector only")
    except Exception as e:
        print(f"Session RF warmup skipped: {e}")

    # One 30-minute chain: fetch from FuelArk, then detect, confirm and report on the
    # data just fetched. Keeping the fetch off the request path matters because a live
    # pull takes ~2 minutes -- request-driven, it would either block the dashboard or
    # leave the cache stale enough that every vehicle reads offline.
    if config.PIPELINE_INTERVAL_SECONDS > 0:
        try:
            from backend.services.pipeline import start_pipeline

            start_pipeline()
        except Exception as e:
            print(f"Pipeline not started: {e}")

    print(f"Backend listening on http://{config.HOST}:{config.PORT}")
    print("API: /api/fleet/summary  /api/fleet/devices  /api/devices/<name>/series")
    app.run(debug=config.DEBUG, port=config.PORT, host=config.HOST, threaded=True)


if __name__ == "__main__":
    main()
