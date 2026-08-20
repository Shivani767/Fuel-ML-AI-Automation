"""The 30-minute chain: fetch from FuelArk, detect, confirm, report.

These used to be two independent timers -- a 15-minute fetch and a 30-minute alert
scan that read whatever happened to be in memory. That meant alerts could run on data
up to 15 minutes stale, and it cost twice the API calls for no benefit. Running them
as one chain guarantees every scan sees data fetched moments earlier.

Cadence and the "online" window are coupled, and getting it wrong is not subtle. A
30-minute fetch plus a ~2-minute run leaves the cache up to ~32 minutes old; with
ACTIVE_ONLINE_MINUTES at 30 every vehicle would fall outside the window and the
dashboard would report the whole fleet inactive. The window is set to 45 to hold a
margin over the real worst case -- see backend/config.py.
"""

from __future__ import annotations

import threading
import time
from datetime import datetime
from typing import Optional

from backend import config

_bg_thread = None
_last_run: Optional[dict] = None
# One cycle at a time: a manual trigger must not overlap the timer and have both
# halves interleave.
_lock = threading.Lock()


def run_once(force_fetch: bool = True) -> dict:
    """Fetch, then detect and alert on what was just fetched."""
    with _lock:
        return _run_once_locked(force_fetch)


def _run_once_locked(force_fetch: bool) -> dict:
    from backend.services import alerts
    from backend.services.fuelark_live import refresh_fleet_live_snapshot, sync_fleet_from_fuelark

    started = time.time()
    out: dict = {"startedAt": datetime.now().isoformat(timespec="seconds")}

    # 0. Roster sync — pick up vehicles newly added on FuelArk Home.
    if config.FUELARK_SYNC_ENABLED:
        t_sync = time.time()
        try:
            sync = sync_fleet_from_fuelark(fetch_new=True)
            out["rosterSync"] = {
                "ok": bool(sync.get("ok")),
                "fuelarkCount": sync.get("fuelarkCount"),
                "addedCount": sync.get("addedCount"),
                "seconds": round(time.time() - t_sync, 1),
                "error": sync.get("error"),
            }
        except Exception as e:
            out["rosterSync"] = {
                "ok": False,
                "error": str(e),
                "seconds": round(time.time() - t_sync, 1),
            }

    # 1. Fetch -- delta only; each device is asked for the minutes since its newest
    #    held point, not the whole day.
    t0 = time.time()
    try:
        snap = refresh_fleet_live_snapshot(force=force_fetch)
        out["fetch"] = {
            "ok": bool(snap.get("ok")),
            "devices": snap.get("devices"),
            "points": snap.get("fetched"),
            "skippedSilent": snap.get("skippedSilent"),
            "seconds": round(time.time() - t0, 1),
        }
    except Exception as e:
        out["fetch"] = {"ok": False, "error": str(e), "seconds": round(time.time() - t0, 1)}
        # Carry on: a failed fetch still leaves the previous snapshot worth scanning,
        # and the confirmation gate drops anything whose device has gone quiet.

    # 2. Detect, confirm, record.
    t1 = time.time()
    try:
        digest = alerts.run_cycle()
        out["alerts"] = {
            "theft": digest.get("theftCount"),
            "fill": digest.get("fillCount"),
            "held": digest.get("heldAwaitingSettle"),
            "suppressed": digest.get("suppressedCount"),
            "days": digest.get("daysWritten"),
            "seconds": round(time.time() - t1, 1),
        }
    except Exception as e:
        out["alerts"] = {"error": str(e), "seconds": round(time.time() - t1, 1)}

    out["totalSeconds"] = round(time.time() - started, 1)
    f, a = out.get("fetch", {}), out.get("alerts", {})
    sync = out.get("rosterSync") or {}
    print(
        f"[pipeline] sync +{sync.get('addedCount', 0)} · "
        f"fetch {f.get('devices', '?')} devices / {f.get('points', '?')} pts "
        f"in {f.get('seconds', '?')}s -> alerts {a.get('theft', '?')} theft / "
        f"{a.get('fill', '?')} fill, {a.get('held', '?')} held, "
        f"{a.get('suppressed', '?')} filtered in {a.get('seconds', '?')}s "
        f"(total {out['totalSeconds']}s)",
        flush=True,
    )

    global _last_run
    _last_run = out
    return out


def last_run() -> Optional[dict]:
    return _last_run


def start_pipeline(interval_sec: Optional[float] = None):
    global _bg_thread
    if _bg_thread is not None and _bg_thread.is_alive():
        return _bg_thread
    interval = float(
        interval_sec if interval_sec is not None else config.PIPELINE_INTERVAL_SECONDS
    )
    if interval <= 0:
        return None

    def _loop():
        # The first cycle runs immediately -- a restart should not leave the cache
        # cold for half an hour, which is what makes the fleet read inactive.
        while True:
            started = time.time()
            try:
                run_once()
            except Exception as e:
                print(f"[pipeline] cycle failed: {e}", flush=True)
            # Hold the cadence regardless of how long the cycle took, rather than
            # sleeping a fixed interval afterwards and drifting.
            time.sleep(max(60.0, interval - (time.time() - started)))

    _bg_thread = threading.Thread(target=_loop, name="fuel-pipeline", daemon=True)
    _bg_thread.start()
    print(
        f"[pipeline] started: fetch + detect + alert every {interval:.0f}s "
        f"(settle {config.ALERT_SETTLE_MINUTES:.0f}min, "
        f"online window {config.ACTIVE_ONLINE_MINUTES:.0f}min)",
        flush=True,
    )
    return _bg_thread
