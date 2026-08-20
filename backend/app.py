"""Flask backend entry — Fuelark-style API for the React frontend."""

from __future__ import annotations

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

    @app.before_request
    def _gate_while_loading():
        from flask import request

        # Health + CORS preflight always answer so the UI can show "loading".
        if request.method == "OPTIONS":
            return None
        path = request.path or ""
        if path == "/api/health" or not path.startswith("/api/"):
            return None
        if data_store.is_ready():
            return None
        if data_store.is_loading() or data_store.load_status().get("error"):
            status = data_store.load_status()
            return jsonify({
                "error": status.get("error")
                or "Backend is still loading May–July telemetry into memory. Retry in a minute.",
                "loading": True,
                "elapsedSec": status.get("elapsedSec"),
            }), 503
        # Not started yet — kick load and say so.
        return jsonify({
            "error": "Backend telemetry load has not finished yet.",
            "loading": True,
        }), 503

    @app.route("/api/health")
    def health():
        status = data_store.load_status()
        code = 200 if status["ready"] else 503
        return jsonify(
            {
                "ok": status["ready"],
                "loading": status["loading"],
                "error": status.get("error"),
                "devices": status.get("devices", 0),
                "points": status.get("points", 0),
                "mapped": len(data_store.device_imei_map) if status["ready"] else 0,
                "elapsedSec": status.get("elapsedSec"),
            }
        ), code

    app.register_blueprint(fleet_bp)
    app.register_blueprint(devices_bp)
    app.register_blueprint(imei_bp)
    app.register_blueprint(live_bp)
    app.register_blueprint(alerts_bp)

    # Optional: serve built frontend from frontend/dist
    dist = ROOT / "frontend/dist"

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


def _after_data_ready():
    try:
        from backend.services.session_ml import load_model

        if load_model():
            print("Session RF model ready (fuel_session_rf.pkl)", flush=True)
        else:
            print("Session RF model not loaded — rule detector only", flush=True)
    except Exception as e:
        print(f"Session RF warmup skipped: {e}", flush=True)

    if config.PIPELINE_INTERVAL_SECONDS > 0:
        try:
            from backend.services.pipeline import start_pipeline

            start_pipeline()
        except Exception as e:
            print(f"Pipeline not started: {e}", flush=True)


def main():
    print(
        "Starting API immediately; loading May + June + July + live in background "
        "(avoids 502 while dumps parse)…",
        flush=True,
    )
    data_store.start_background_load(on_ready=_after_data_ready)

    print(f"Backend listening on http://{config.HOST}:{config.PORT}", flush=True)
    print("API: /api/fleet/summary  /api/fleet/devices  /api/devices/<name>/series", flush=True)
    app.run(debug=config.DEBUG, port=config.PORT, host=config.HOST, threaded=True)


if __name__ == "__main__":
    main()
