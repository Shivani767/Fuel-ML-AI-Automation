"""Backend configuration — paths resolve to project root."""

from __future__ import annotations

import os
from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = PACKAGE_DIR.parent

FUEL_DATA_DIR = Path(os.environ.get("FUEL_DATA_DIR", PROJECT_ROOT / "fuel_data"))
DEVICE_IMEI_MAP_PATH = FUEL_DATA_DIR / "device_imei_map.json"
MAY_DATA_PATH = FUEL_DATA_DIR / "may_2026_data.json"
JUNE_DATA_PATH = FUEL_DATA_DIR / "june_2026_data.json"
LIVE_DATA_PATH = FUEL_DATA_DIR / "live_fuel_data.json"
DAILY_DIR = FUEL_DATA_DIR / "daily"
# Comma list of monthly dump stems to load (may_2026_data.json → "may").
# July early days come from daily/*.json.gz; mid-July+ from live overlay.
LOAD_MONTH_DUMPS = [
    m.strip().lower()
    for m in os.environ.get("LOAD_MONTH_DUMPS", "may,june").split(",")
    if m.strip()
]
EVENTS_PATH = FUEL_DATA_DIR / "fuel_events.json"
ML_ALERTS_PATH = FUEL_DATA_DIR / "ml_alerts.json"
FLEET_IMEIS_PATH = FUEL_DATA_DIR / "fleet_imeis.txt"

HOST = os.environ.get("BACKEND_HOST", "0.0.0.0")
PORT = int(os.environ.get("BACKEND_PORT", "5001"))
DEBUG = os.environ.get("BACKEND_DEBUG", "0") == "1"

# Inactive if last point older than this many hours (legacy / non-online mode)
INACTIVE_HOURS = float(os.environ.get("FLEET_INACTIVE_HOURS", "24"))
# Fuelark-style Active = last telemetry within this many minutes (not calendar-today).
# Must exceed the pipeline cadence plus a cycle's runtime, or the cache ages out
# between fetches and every vehicle reads inactive. At a 30-min cadence a cycle can
# finish ~32 min after the previous fetch, so 45 holds a real margin.
ACTIVE_ONLINE_MINUTES = float(os.environ.get("FLEET_ACTIVE_ONLINE_MINUTES", "5"))
# Running matches FuelArk Live: any reported speed > this (ignition not required).
RUNNING_SPEED_KMH = float(os.environ.get("FLEET_RUNNING_SPEED", "0"))
# Prefer FuelArk Helper/DeviceStatus for dashboard KPI cards when reachable.
FUELARK_KPI_LIVE = os.environ.get("FUELARK_KPI_LIVE", "1").lower() in ("1", "true", "yes")
# --- the 30-minute pipeline: fetch -> detect -> confirm -> alert ---
# One cadence for the whole chain, so every scan runs on data fetched moments before.
# Replaces the old split of a 15-min fetch and a separate 30-min alert timer, which
# cost twice the API calls and scanned data up to 15 min stale. 0 disables.
PIPELINE_INTERVAL_SECONDS = float(os.environ.get("PIPELINE_INTERVAL_SECONDS", "1800"))
# Retained so POST /api/alerts/run and any standalone use still have a value.
ALERT_INTERVAL_SECONDS = float(os.environ.get("ALERT_INTERVAL_SECONDS", "1800"))
# Hold an event back until its drop has persisted this long. 68% of >2L fuel dips at
# ignition start recover within ~30 min, so waiting is what separates them from theft.
ALERT_SETTLE_MINUTES = float(os.environ.get("ALERT_SETTLE_MINUTES", "25"))
# Rolling detection window for Scan now / agent cycle (calibrated lookback).
ALERT_WINDOW_HOURS = float(os.environ.get("ALERT_WINDOW_HOURS", "4"))
# 30-min slab: feed this many hours of telemetry ending at the slab end, then
# keep only events that start inside the 30-minute prediction window.
SLAB_LOOKBACK_HOURS = float(os.environ.get("SLAB_LOOKBACK_HOURS", "4"))
# Fraction of the original drop that must still be missing after the settle period.
ALERT_PERSIST_FRACTION = float(os.environ.get("ALERT_PERSIST_FRACTION", "0.70"))
# Ignition OFF->ON this close to a session start makes it a crank-transient suspect.
IGNITION_QUARANTINE_MINUTES = float(os.environ.get("IGNITION_QUARANTINE_MINUTES", "5"))
# A drop must exceed this multiple of the device's voltage-linked phantom swing.
VOLTAGE_SWING_MULTIPLE = float(os.environ.get("VOLTAGE_SWING_MULTIPLE", "2.0"))

# --- parking on a gradient -------------------------------------------------------
# Fuel settles away from the sender when a vehicle stops on a slope: the level steps
# down within a minute or two of ignition OFF, holds flat while parked, and comes back
# only when it drives off -- hours later, long past the settle window, so the
# persistence test cannot separate it from theft. A measured case: a 15 L step down on
# parking, flat for 3 h, fully restored on departure, released as AUTO_CONFIRMED theft.
#
# There is no tilt signal to lean on. The Teltonika axis IDs (iO_17/18/19 = Axis X/Y/Z)
# are present in the API schema but never populated -- 0 non-empty samples across 46
# device-days -- and `altitude` gives ground elevation, not vehicle attitude. So the
# discriminator is behavioural: stolen fuel does not come back when you drive away.
#
# Drains starting this close to the vehicle coming to rest are gradient suspects.
PARK_SETTLE_MINUTES = float(os.environ.get("PARK_SETTLE_MINUTES", "5"))
# Parking is detected from motion, not only from an ignition ON->OFF edge: iO_1 is
# provably stuck off on some devices while they drive (one logged 1,015 moving ticks
# and 119.8 km with iO_1 == 0 all day), and those devices never emit the edge at all.
# Consecutive fixes further apart than this mean the vehicle relocated, which catches
# movement even when speed reports 0 between sparse polls. Well above parked GPS
# jitter, which is tens of metres.
MOTION_DISPLACEMENT_M = float(os.environ.get("MOTION_DISPLACEMENT_M", "100"))
# How long such a drain may be held waiting for the vehicle to move before giving up
# and reporting it anyway. This is the cost of the check: a theft in the first few
# minutes after parking can be delayed by up to this long. It fails toward reporting.
PARK_ARTIFACT_MAX_HOLD_MINUTES = float(
    os.environ.get("PARK_ARTIFACT_MAX_HOLD_MINUTES", "120")
)
# Retroactive cancellation of the drain/fill pair a gradient produces. Matched on
# place, not just time: settling reverses exactly where it began, whereas a theft
# followed by a genuine refuel almost always happens at a pump somewhere else.
SETTLING_MATCH_HOURS = float(os.environ.get("SETTLING_MATCH_HOURS", "24"))
SETTLING_MATCH_RADIUS_M = float(os.environ.get("SETTLING_MATCH_RADIUS_M", "60"))
# Tighter than the 90-minute bounce rule's 0.35, because a 24 h window would otherwise
# start cancelling real theft-then-refuel pairs.
SETTLING_MATCH_TOLERANCE = float(os.environ.get("SETTLING_MATCH_TOLERANCE", "0.25"))
# A telemetry gap this long inside a session means the change was never observed.
ALERT_MAX_GAP_MINUTES = float(os.environ.get("ALERT_MAX_GAP_MINUTES", "20"))
# Never imply "no theft" for a device that simply stopped reporting.
ALERT_STALE_DEVICE_MINUTES = float(os.environ.get("ALERT_STALE_DEVICE_MINUTES", "60"))
ALERT_MIN_DECISION = os.environ.get("ALERT_MIN_DECISION", "AUTO_CONFIRMED")
# --- precision gates (alert agent only; Live Map detection can stay more sensitive) ---
# Floor for theft alerts. Detection may still find 2–5 L sessions for review, but the
# 30-min agent should not page humans for ambiguous small drains.
ALERT_THEFT_MIN_LITERS = float(os.environ.get("ALERT_THEFT_MIN_LITERS", "5.0"))
# A huge liter jump in a few seconds is almost always a sensor spike, not siphoning.
ALERT_SPIKE_MAX_MINUTES = float(os.environ.get("ALERT_SPIKE_MAX_MINUTES", "0.5"))
ALERT_SPIKE_MIN_LITERS = float(os.environ.get("ALERT_SPIKE_MIN_LITERS", "8.0"))
# Fuel fills of this many liters or less are not events at all: on this sensor a
# sub-10 L rise is slope rebound, tank slosh or sender drift rather than a pump.
# The floor is a hard minimum -- callers and the API may raise it, never lower it.
ALERT_FILL_MIN_LITERS = max(
    10.0, float(os.environ.get("ALERT_FILL_MIN_LITERS", "10.0"))
)
# Filling while driving at highway/site travel speed is not a pump event.
ALERT_FILL_MAX_SPEED_KMH = float(os.environ.get("ALERT_FILL_MAX_SPEED_KMH", "15.0"))
# Ignition-ON + moving short drains are the noisiest false thefts on this fleet.
# Require parked or ignition-off, or a longer observed session.
ALERT_THEFT_MIN_DURATION_MOVING_MIN = float(
    os.environ.get("ALERT_THEFT_MIN_DURATION_MOVING_MIN", "3.0")
)
# Sustained / large drains while slowly moving (site work) still count as theft —
# FuelArk marks them. Below this volume, short moving drains stay suppressed.
ALERT_THEFT_KEEP_MOVING_MIN_LITERS = float(
    os.environ.get("ALERT_THEFT_KEEP_MOVING_MIN_LITERS", "8.0")
)
# If session RF strongly says noise/fault, do not alert even if rules said AUTO.
ALERT_ML_DISAGREE_MIN = float(os.environ.get("ALERT_ML_DISAGREE_MIN", "0.70"))

# RETIRED as an independent timer -- the fetch now runs inside the pipeline (see
# PIPELINE_INTERVAL_SECONDS). Kept only for callers that still start a standalone
# refresher directly; nothing in backend.app does.
FLEET_BG_REFRESH_SECONDS = float(os.environ.get("FLEET_BG_REFRESH_SECONDS", "0"))
# External voltage (iO_66 mV) below this → Battery Disconnected soft alert
BATTERY_DISCONNECT_MV = float(os.environ.get("FLEET_BATTERY_DISCONNECT_MV", "11000"))
ROSTER_PATH = FUEL_DATA_DIR / "fuelark_vehicle_roster.json"
# Auto-pull new vehicles from the FuelArk account (web1 / api1 Helper/GetDevices).
FUELARK_SYNC_ENABLED = os.environ.get("FUELARK_SYNC_ENABLED", "1").lower() in (
    "1", "true", "yes",
)
FUELARK_SYNC_FETCH_DAYS = int(os.environ.get("FUELARK_SYNC_FETCH_DAYS", "3"))
FUELARK_SYNC_MAX_NEW = int(os.environ.get("FUELARK_SYNC_MAX_NEW", "20"))

# Default series downsample for chart (keep every Nth point after sort)
DEFAULT_DOWNSAMPLE = int(os.environ.get("SERIES_DOWNSAMPLE", "20"))
MAX_SERIES_POINTS = int(os.environ.get("MAX_SERIES_POINTS", "5000"))
