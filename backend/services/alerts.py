"""30-minute fill & theft alert agent.

Detection already finds events; the job here is deciding which ones a human should
be told about. Measurement on this fleet showed three ways a confident-looking event
turns out to be nothing:

  * Ignition start. Of fuel dips >2 L at an OFF->ON transition, 68% fully recover
    within ~30 min. Cranking sags the supply rail (median -0.66 V, worst -12.9 V) and
    the sensor follows it down. Dips as large as 23.9 L came back to within 0.7 L.
  * Voltage coupling. On 12 profiled devices the reading tracks the rail closely
    enough to manufacture litres out of nothing -- 70 L of phantom swing on the worst.
    See backend/services/voltage_profile.py.
  * Reporting gaps. One device went dark for 206 minutes across a 17.3 L change.
    Nothing was observed, so nothing can be concluded.

Running every 30 minutes is what makes these tractable: the agent can wait for a drop
to prove it is permanent before reporting it. That costs up to ~30 min of notice on a
real theft and removes nearly all of the false alarms.

Events that are merely too young are HELD and reconsidered next cycle. Events that
fail a substantive check are recorded with the reason rather than silently dropped --
a filter nobody can inspect is a filter nobody trusts.
"""

from __future__ import annotations

import csv
import json
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from backend import config
from backend.services import data_store, metrics, voltage_profile

ALERT_DIR = config.PROJECT_ROOT / "fuel_data" / "alerts"
LEDGER_PATH = ALERT_DIR / "ledger.json"
LATEST_PATH = ALERT_DIR / "latest.json"

# One CSV per calendar day, filed by when the event happened rather than when the
# agent noticed it -- an event at 23:50 confirmed after midnight still belongs to
# the day it occurred, which is what a day-wise report is asking about.
DAILY_DIR = ALERT_DIR / "daily"

LEDGER_RETENTION_DAYS = 7
# Per-cycle digests kept on disk (~2 days at a 30-minute cadence).
DIGEST_KEEP = 96
# Context read either side of an ad-hoc slab scan (see scan_window).
SLAB_CONTEXT_HOURS = 1.0

CSV_FIELDS = [
    "confirmedAt", "deviceName", "imei", "eventType", "eventTime", "endTime",
    "volume_L", "confidence", "decision", "ignition", "trulyParked", "tamper",
    "durationMinutes", "latitude", "longitude",
]
# Filtered events keep the same shape plus the reason, so the two files can be
# compared column-for-column when auditing why something never alerted.
FILTERED_FIELDS = CSV_FIELDS + ["suppressedReason"]


def alerts_csv_for(day: str) -> Path:
    return DAILY_DIR / f"{day}_alerts.csv"


def filtered_csv_for(day: str) -> Path:
    return DAILY_DIR / f"{day}_filtered.csv"


def _event_day(ev: dict) -> str:
    """Calendar day an event belongs to, from its own timestamp."""
    ts = str(ev.get("eventTime") or "")[:10]
    return ts if len(ts) == 10 else datetime.now().strftime("%Y-%m-%d")


def _append_rows(path: Path, rows: List[dict], fields: List[str]) -> None:
    if not rows:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    new_file = not path.exists()
    with path.open("a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        if new_file:
            w.writeheader()
        w.writerows(rows)


def write_daily_csvs(released: List[dict], suppressed: List[dict]) -> List[str]:
    """Append events to their own day's file. Returns the days touched."""
    by_day_rel: Dict[str, List[dict]] = {}
    by_day_sup: Dict[str, List[dict]] = {}
    for e in released:
        by_day_rel.setdefault(_event_day(e), []).append({**e, "volume_L": e.get("volume")})
    for e in suppressed:
        by_day_sup.setdefault(_event_day(e), []).append({**e, "volume_L": e.get("volume")})

    for day, rows in by_day_rel.items():
        _append_rows(alerts_csv_for(day), rows, CSV_FIELDS)
    for day, rows in by_day_sup.items():
        _append_rows(filtered_csv_for(day), rows, FILTERED_FIELDS)

    return sorted(set(by_day_rel) | set(by_day_sup))


def read_daily_alerts(day: Optional[str] = None, include_filtered: bool = False) -> List[dict]:
    """Rows for one day, or every day when `day` is None."""
    if not DAILY_DIR.exists():
        return []
    pattern = f"{day}_*.csv" if day else "*.csv"
    out: List[dict] = []
    for path in sorted(DAILY_DIR.glob(pattern)):
        filtered = path.name.endswith("_filtered.csv")
        if filtered and not include_filtered:
            continue
        try:
            with path.open(encoding="utf-8") as f:
                for row in csv.DictReader(f):
                    row["status"] = "filtered" if filtered else "released"
                    row["day"] = path.name[:10]
                    out.append(row)
        except OSError:
            continue
    return out


def available_days() -> List[str]:
    if not DAILY_DIR.exists():
        return []
    return sorted({p.name[:10] for p in DAILY_DIR.glob("*_alerts.csv")}, reverse=True)

DECISION_RANK = {"LIKELY_NOISE": 0, "MANUAL_REVIEW": 1, "AUTO_CONFIRMED": 2, "VERIFIED": 3}

_bg_thread = None
_last_digest: Optional[dict] = None

# One cycle at a time. The timer thread and POST /api/alerts/run can otherwise both
# load the ledger before either saves it, and every event gets reported twice.
_cycle_lock = threading.Lock()


# --------------------------------------------------------------------------- utils

def _parse_ts(value) -> Optional[datetime]:
    """Parse to a NAIVE datetime.

    Timestamps reach this from several places: backend points carry naive ISO
    strings, while detector session rows are tz-aware IST. Everything downstream
    compares against a naive `datetime.now()`, so normalise here rather than let an
    aware value blow up the arithmetic.
    """
    if not value:
        return None
    if isinstance(value, datetime):
        return value.replace(tzinfo=None) if value.tzinfo else value
    s = str(value)
    for n in (32, 26, 19, 16, 10):
        try:
            dt = datetime.fromisoformat(s[:n])
        except ValueError:
            continue
        return dt.replace(tzinfo=None) if dt.tzinfo else dt
    return None


def _event_key(device: str, ev: dict) -> str:
    """Stable identity across cycles: (device, type, start-minute).

    Volume is deliberately NOT part of the key. detection._dedupe includes it, but
    that runs inside a single cycle where it separates genuinely distinct events. Held
    across cycles it does the opposite: a drain still in progress keeps its start time
    while its magnitude grows, so a volume-bearing key minted a fresh identity every
    30 minutes and re-alerted the same theft repeatedly. A session is one direction at
    one location, so device + type + start-minute already identifies it uniquely.
    """
    return "|".join([
        device,
        str(ev.get("eventType")),
        str(ev.get("eventTime"))[:16],
    ])


# -------------------------------------------------------------------------- ledger

def load_ledger() -> dict:
    if LEDGER_PATH.exists():
        try:
            return json.loads(LEDGER_PATH.read_text())
        except (OSError, ValueError):
            pass
    return {}


def save_ledger(ledger: dict) -> None:
    cutoff = (datetime.now() - timedelta(days=LEDGER_RETENTION_DAYS)).isoformat()
    pruned = {k: v for k, v in ledger.items() if str(v.get("confirmedAt", "")) >= cutoff}
    ALERT_DIR.mkdir(parents=True, exist_ok=True)
    LEDGER_PATH.write_text(json.dumps(pruned, indent=1, sort_keys=True), encoding="utf-8")


# ------------------------------------------------------------------ telemetry views

def _series_for(device: str) -> List[dict]:
    """Snapshot of a device's points.

    Copied rather than referenced: the 15-minute fleet refresher mutates
    device_data_dict from another thread while this iterates.
    """
    return list(data_store.device_data_dict.get(device, ()))


def _level_at(series: List[dict], when: datetime, window_min: float = 6.0) -> Optional[float]:
    """Median fuel level near a timestamp; None when nothing was reported nearby."""
    lo, hi = when - timedelta(minutes=window_min), when + timedelta(minutes=window_min)
    vals = []
    for p in series:
        ts = _parse_ts(p.get("time"))
        if ts and lo <= ts <= hi:
            try:
                vals.append(float(p.get("fuelInLiter") or 0))
            except (TypeError, ValueError):
                continue
    if not vals:
        return None
    vals.sort()
    return vals[len(vals) // 2]


def _max_gap_minutes(series: List[dict], start: datetime, end: datetime) -> float:
    """Longest unobserved stretch inside [start, end].

    The window edges count. Measuring only the gaps *between* reported points misses
    a window that simply goes silent partway through -- which is precisely the
    blackout case this guards against.
    """
    ts = sorted(t for t in (_parse_ts(p.get("time")) for p in series) if t and start <= t <= end)
    if not ts:
        return (end - start).total_seconds() / 60.0
    gaps = [(ts[0] - start).total_seconds() / 60.0,
            (end - ts[-1]).total_seconds() / 60.0]
    gaps += [(ts[i + 1] - ts[i]).total_seconds() / 60.0 for i in range(len(ts) - 1)]
    return max(gaps)


def _refill_between(series: List[dict], start: datetime, end: datetime,
                    min_step: float = 3.0) -> float:
    """Litres added back between two times, summing sustained upward steps.

    Only counts rises that hold for a few readings, so sensor jitter and the
    voltage-driven oscillation do not masquerade as refuelling.
    """
    pts = sorted(
        ((t, v) for t, v in (
            (_parse_ts(p.get("time")),
             float(p.get("fuelInLiter") or 0) if p.get("fuelInLiter") is not None else None)
            for p in series)
         if t and v is not None and start <= t <= end),
        key=lambda x: x[0],
    )
    if len(pts) < 4:
        return 0.0
    vals = [v for _, v in pts]
    total = 0.0
    i = 0
    while i < len(vals) - 3:
        rise = vals[i + 1] - vals[i]
        if rise >= min_step:
            # sustained only if the new level holds over the next couple of readings
            if min(vals[i + 1:i + 4]) >= vals[i] + min_step * 0.6:
                total += rise
                i += 3
                continue
        i += 1
    return total


def _ignition_started_near(series: List[dict], when: datetime, minutes: float) -> bool:
    """Was there an ignition OFF->ON transition within `minutes` of `when`?"""
    lo, hi = when - timedelta(minutes=minutes), when + timedelta(minutes=minutes)
    prev_on = None
    for p in sorted(series, key=lambda x: str(x.get("time"))):
        ts = _parse_ts(p.get("time"))
        if not ts:
            continue
        on = bool(int(p.get("iO_1") or 0))
        if prev_on is False and on and lo <= ts <= hi:
            return True
        prev_on = on
    return False


def _ignition_stopped_near(series: List[dict], when: datetime, minutes: float) -> bool:
    """Was there an ignition ON->OFF transition within `minutes` of `when`?

    The mirror of _ignition_started_near. Parking is what triggers fuel to settle on a
    gradient, so this marks the drains that need the departure test below.
    """
    lo, hi = when - timedelta(minutes=minutes), when + timedelta(minutes=minutes)
    prev_on = None
    for p in sorted(series, key=lambda x: str(x.get("time"))):
        ts = _parse_ts(p.get("time"))
        if not ts:
            continue
        on = bool(int(p.get("iO_1") or 0))
        if prev_on is True and not on and lo <= ts <= hi:
            return True
        prev_on = on
    return False


def _tick_in_motion(p: dict) -> bool:
    """Was the vehicle moving at this tick, judged without trusting iO_1?"""
    try:
        if float(p.get("speed") or 0) > config.RUNNING_SPEED_KMH:
            return True
    except (TypeError, ValueError):
        pass
    for key in ("iO_2", "iO_6"):          # accelerometer / movement IOs
        try:
            if int(p.get(key) or 0):
                return True
        except (TypeError, ValueError):
            continue
    return False


def _motion_flags(series: List[dict]) -> List[Tuple[datetime, bool]]:
    """[(timestamp, moving)] in time order, from speed, movement IOs and displacement.

    Ignition is not consulted. iO_1 is stuck off on some devices while they drive, and
    stuck on for others, so an ignition edge is not a reliable "the vehicle stopped"
    signal. Displacement between consecutive fixes catches relocation even when speed
    reads 0 across sparse polls.
    """
    pts = []
    for p in series:
        ts = _parse_ts(p.get("time"))
        if ts is not None:
            pts.append((ts, p))
    pts.sort(key=lambda x: x[0])

    out: List[Tuple[datetime, bool]] = []
    prev_fix = None
    for ts, p in pts:
        moving = _tick_in_motion(p)
        fix = None
        try:
            lat, lon = p.get("latitude"), p.get("longitude")
            if lat not in (None, "") and lon not in (None, ""):
                fix = (float(lat), float(lon))
        except (TypeError, ValueError):
            fix = None
        if not moving and fix is not None and prev_fix is not None:
            moved = metrics.haversine_m(prev_fix[0], prev_fix[1], fix[0], fix[1])
            if moved is not None and moved > config.MOTION_DISPLACEMENT_M:
                moving = True
        if fix is not None:
            prev_fix = fix
        out.append((ts, moving))
    return out


def _came_to_rest_near(flags: List[Tuple[datetime, bool]], when: datetime,
                       minutes: float) -> bool:
    """Did the vehicle transition from moving to stationary within `minutes` of `when`?

    This is what makes gradient detection work on the stuck-ignition devices: they
    never emit an ON->OFF edge, so an ignition-only test left them unprotected.
    """
    lo, hi = when - timedelta(minutes=minutes), when + timedelta(minutes=minutes)
    prev_moving = None
    for ts, moving in flags:
        if prev_moving is True and not moving and lo <= ts <= hi:
            return True
        prev_moving = moving
    return False


def _moved_after(flags: List[Tuple[datetime, bool]], when: datetime,
                 until: Optional[datetime] = None) -> Optional[datetime]:
    """First time after `when` the vehicle physically moved.

    Motion rather than ignition: physical movement is what un-settles fuel on a
    gradient, and a device with iO_1 stuck on would otherwise read as departed
    immediately -- losing the hold that makes the check work.

    `until` bounds the lookahead so a decision never depends on telemetry that had not
    arrived yet -- the live snapshot ends at now, but callers and tests may hand over a
    fuller series, and the answer must not change because of it.
    """
    for ts, moving in flags:
        if ts <= when or (until is not None and ts > until):
            continue
        if moving:
            return ts
    return None


def _latest_ts(series: List[dict]) -> Optional[datetime]:
    return _parse_ts(series[-1].get("time")) if series else None


# ---------------------------------------------------------------- confirmation gate

def confirm_event(device: str, imei: str, ev: dict, series: List[dict],
                  now: Optional[datetime] = None,
                  require_reporting: bool = True) -> Tuple[str, str]:
    """Return (status, reason) where status is release | hold | suppress.

    `require_reporting` guards the live sweep's rule that a silent device proves
    nothing. A retrospective scan of a past window is asking a different question --
    the telemetry for that window is in hand -- so it turns the rule off rather than
    suppress every event on a vehicle that happens to be offline right now.
    """
    now = now or datetime.now()
    start = _parse_ts(ev.get("eventTime"))
    end = _parse_ts(ev.get("endTime")) or start
    if start is None:
        return "suppress", "unparseable_time"

    volume = abs(float(ev.get("volume") or 0))
    is_theft = "theft" in str(ev.get("eventType", "")).lower()

    # 0. Human-confirmed cases bypass every artifact filter. They come from the
    #    verified spreadsheet, are stamped at midnight with no surrounding telemetry,
    #    and would fail the baseline test purely for that reason -- but their truth
    #    was established by a person, not by this pipeline.
    if str(ev.get("decision")) == "VERIFIED":
        return "release", ""

    # 1. Device must still be reporting -- silence is not evidence of anything.
    if require_reporting:
        last = _latest_ts(series)
        if last is None or (now - last).total_seconds() / 60.0 > config.ALERT_STALE_DEVICE_MINUTES:
            return "suppress", "device_not_reporting"

    # 2. Too young to judge -- revisit next cycle rather than discard.
    settle = timedelta(minutes=config.ALERT_SETTLE_MINUTES)
    if now - end < settle:
        return "hold", "awaiting_settle"

    # 3. The change has to have actually been observed.
    gap = _max_gap_minutes(series, start - timedelta(minutes=5), end + settle)
    if gap > config.ALERT_MAX_GAP_MINUTES:
        return "suppress", f"unobserved_gap_{gap:.0f}min"

    # Fills need no persistence test: fuel that appeared is not a safety concern and
    # a phantom fill costs nothing. Everything below is about drains.
    if not is_theft:
        return "release", ""

    # 4. Voltage coupling can manufacture litres. Require a real margin over it.
    swing = voltage_profile.swing_liters(imei)
    if swing > 0 and volume < swing * config.VOLTAGE_SWING_MULTIPLE:
        return "suppress", f"voltage_artifact_swing_{swing:.1f}L"

    # 5. Did the fuel actually stay gone?
    #    Refuelling between the theft and the settle point legitimately restores the
    #    level, so credit any rise back. Without this a genuine theft followed by a
    #    fill looks identical to a sensor blip that recovered.
    before = _level_at(series, start - timedelta(minutes=8))
    after = _level_at(series, end + settle)
    if before is None or after is None:
        return "suppress", "no_baseline_for_persistence"
    refilled = _refill_between(series, end, end + settle)
    still_missing = before - after + refilled
    if still_missing < volume * config.ALERT_PERSIST_FRACTION:
        detail = f"recovered_{still_missing:.1f}L_of_{volume:.1f}L"
        if refilled > 0:
            detail += f"_after_{refilled:.1f}L_refill"
        return "suppress", detail

    # 6. Crank transient: only accept it if the loss is unmistakable.
    if _ignition_started_near(series, start, config.IGNITION_QUARANTINE_MINUTES):
        floor = max(swing * 3.0, 5.0)
        if volume < floor:
            return "suppress", f"ignition_transient_below_{floor:.1f}L"

    # 7. Parking on a gradient. Fuel settles away from the sender when a vehicle stops
    #    on a slope: the level steps down within a minute or two of ignition OFF, holds
    #    flat while parked, and returns only when it drives off -- hours later, well
    #    past the settle window, so step 5 above cannot tell it from theft. No tilt
    #    signal exists on this fleet (see PARK_SETTLE_MINUTES in backend/config.py), so
    #    use the one discriminator that does hold: stolen fuel does not come back when
    #    you drive away. Wait for the departure, bounded, and fail toward reporting.
    #    Parking is detected from motion as well as from the ignition edge, because the
    #    stuck-off-iO_1 devices never emit that edge and would otherwise get no
    #    gradient protection at all.
    flags = _motion_flags(series)
    parked_near_start = (
        _came_to_rest_near(flags, start, config.PARK_SETTLE_MINUTES)
        or _ignition_stopped_near(series, start, config.PARK_SETTLE_MINUTES)
    )
    if parked_near_start:
        moved_at = _moved_after(flags, end, until=now)
        within_hold = (now - end) < timedelta(minutes=config.PARK_ARTIFACT_MAX_HOLD_MINUTES)
        if moved_at is None:
            # Still parked. Nothing has happened yet that could settle the question.
            if within_hold:
                return "hold", "awaiting_departure"
        else:
            settled_at = moved_at + timedelta(minutes=10)
            restored = _level_at(series, settled_at)
            if restored is None:
                if within_hold and now < settled_at:
                    return "hold", "awaiting_departure_settle"
            elif restored >= before - volume * (1.0 - config.ALERT_PERSIST_FRACTION):
                return "suppress", f"park_settling_restored_to_{restored:.1f}L"

    return "release", ""


# ----------------------------------------------------------------------- the cycle

def run_cycle(now: Optional[datetime] = None, devices: Optional[List[str]] = None) -> dict:
    """One pass over the fleet. Returns the digest. Serialised against itself."""
    with _cycle_lock:
        return _run_cycle_locked(now, devices)


def _run_cycle_locked(now: Optional[datetime], devices: Optional[List[str]]) -> dict:
    from backend.services.detection import detect_fill_theft

    now = now or datetime.now()
    started = time.time()
    window_from = now - timedelta(hours=config.ALERT_WINDOW_HOURS)
    min_rank = DECISION_RANK.get(config.ALERT_MIN_DECISION, 2)

    ledger = load_ledger()
    released: List[dict] = []
    suppressed: List[dict] = []
    held = 0
    skipped_devices = 0

    names = devices if devices is not None else sorted(data_store.device_data_dict.keys())
    for device in names:
        series = _series_for(device)
        if len(series) < 10:
            skipped_devices += 1
            continue
        recent = [p for p in series if (_parse_ts(p.get("time")) or datetime.min) >= window_from]
        if len(recent) < 10:
            skipped_devices += 1
            continue

        imei = str(recent[-1].get("imei") or "")
        try:
            events = detect_fill_theft(device, recent)
        except Exception as e:
            print(f"[alerts] {device}: detection failed: {e}")
            continue

        for ev in events:
            if DECISION_RANK.get(str(ev.get("decision")), 0) < min_rank:
                continue
            key = _event_key(device, ev)
            prior = ledger.get(key)
            if prior is not None:
                # Already reported. A drain that kept going since then has a larger
                # magnitude now; record the bigger figure so the ledger reflects the
                # event's real size without alerting a second time for it.
                vol = float(ev.get("volume") or 0)
                if vol > float(prior.get("volume") or 0):
                    prior["volume"] = ev.get("volume")
                    prior["revisedAt"] = now.isoformat(timespec="seconds")
                continue

            status, reason = confirm_event(device, imei, ev, series, now=now)
            if status == "hold":
                held += 1
                continue

            row = dict(ev)
            row["deviceName"] = device
            row["imei"] = imei
            if status == "suppress":
                row["suppressedReason"] = reason
                suppressed.append(row)
                continue

            row["confirmedAt"] = now.isoformat(timespec="seconds")
            released.append(row)
            ledger[key] = {
                "confirmedAt": row["confirmedAt"],
                "deviceName": device,
                "eventType": ev.get("eventType"),
                "volume": ev.get("volume"),
            }

    save_ledger(ledger)
    digest = _write_digest(now, released, suppressed, held, skipped_devices,
                           len(names), time.time() - started)
    global _last_digest
    _last_digest = digest
    return digest


def _build_digest(now, released, suppressed, held, skipped, n_devices, took) -> dict:
    """Shape a digest without persisting it. Split out from `_write_digest` so a
    read-only scan (see `scan_window`) returns the same structure the UI already
    renders, while touching nothing on disk."""
    thefts = [e for e in released if "theft" in str(e.get("eventType", "")).lower()]
    fills = [e for e in released if e not in thefts]

    by_vehicle: Dict[str, dict] = {}
    for e in released:
        v = by_vehicle.setdefault(
            e["deviceName"], {"deviceName": e["deviceName"], "imei": e.get("imei", ""),
                              "theftCount": 0, "theftLiters": 0.0,
                              "fillCount": 0, "fillLiters": 0.0}
        )
        vol = float(e.get("volume") or 0)
        if "theft" in str(e.get("eventType", "")).lower():
            v["theftCount"] += 1
            v["theftLiters"] = round(v["theftLiters"] + vol, 2)
        else:
            v["fillCount"] += 1
            v["fillLiters"] = round(v["fillLiters"] + vol, 2)

    reasons: Dict[str, int] = {}
    for s in suppressed:
        r = str(s.get("suppressedReason", "")).split("_swing_")[0].split("_of_")[0]
        reasons[r] = reasons.get(r, 0) + 1

    digest = {
        "generatedAt": now.isoformat(timespec="seconds"),
        "windowHours": config.ALERT_WINDOW_HOURS,
        "settleMinutes": config.ALERT_SETTLE_MINUTES,
        "minDecision": config.ALERT_MIN_DECISION,
        "devicesScanned": n_devices,
        "devicesSkipped": skipped,
        "theftCount": len(thefts),
        "theftLiters": round(sum(float(e.get("volume") or 0) for e in thefts), 2),
        "fillCount": len(fills),
        "fillLiters": round(sum(float(e.get("volume") or 0) for e in fills), 2),
        "heldAwaitingSettle": held,
        "suppressedCount": len(suppressed),
        "suppressedByReason": reasons,
        "byVehicle": sorted(by_vehicle.values(), key=lambda v: -v["theftLiters"]),
        "thefts": thefts,
        "fills": fills,
        "suppressed": suppressed,
        "tookSeconds": round(took, 1),
    }
    return digest


def _write_digest(now, released, suppressed, held, skipped, n_devices, took) -> dict:
    digest = _build_digest(now, released, suppressed, held, skipped, n_devices, took)
    reasons = digest["suppressedByReason"]

    ALERT_DIR.mkdir(parents=True, exist_ok=True)
    LATEST_PATH.write_text(json.dumps(digest, indent=1, default=str), encoding="utf-8")
    stamp = now.strftime("%Y%m%dT%H%M%S")
    (ALERT_DIR / f"digest_{stamp}.json").write_text(
        json.dumps(digest, indent=1, default=str), encoding="utf-8"
    )
    # A digest per cycle is 48 files a day. They are a debugging aid, not the record
    # -- the day-wise CSVs are -- so keep only a recent window.
    digests = sorted(ALERT_DIR.glob("digest_*.json"))
    for stale in digests[:-DIGEST_KEEP]:
        try:
            stale.unlink()
        except OSError:
            pass

    days = write_daily_csvs(released, suppressed)
    digest["daysWritten"] = days

    print(f"[alerts] {digest['theftCount']} theft / {digest['fillCount']} fill released; "
          f"{held} held, {len(suppressed)} suppressed {reasons or ''}; "
          f"{n_devices - skipped} devices in {digest['tookSeconds']}s"
          + (f"; csv -> {', '.join(days)}" if days else ""))
    return digest


def scan_window(window_from: datetime, window_to: datetime,
                devices: Optional[List[str]] = None) -> dict:
    """Detect fills and thefts inside one explicit window. Read-only.

    Deliberately NOT `run_cycle` with a different clock. Two things make the live
    cycle wrong for answering "what happened between 12:00 and 12:30":

      * it consults the ledger, whose whole job is to stop an event being reported
        twice -- so any slab the agent has already swept would come back empty,
        which is exactly the slab someone goes looking for; and
      * it writes: the ledger, the digest files and the day-wise CSVs. Inspecting a
        past window must not rewrite the record of it.

    So this shares the detector and the confirmation gate but keeps no state.
    """
    from backend.services.detection import detect_fill_theft

    started = time.time()
    now = datetime.now()
    min_rank = DECISION_RANK.get(config.ALERT_MIN_DECISION, 2)

    # Read wider than the slab. A session that opens before it or closes after it is
    # still the slab's event, and 30 minutes of points on their own rarely give the
    # detector enough to establish a baseline. Events are narrowed to the slab below.
    #
    # An hour either side puts a 30-minute slab in a 2.5h window -- about what the live
    # cycle detects over (ALERT_WINDOW_HOURS = 3), so findings stay comparable. Padding
    # by the full window instead would more than double the points fed to the detector
    # on every device, and this runs while someone waits on a button.
    pad = timedelta(hours=SLAB_CONTEXT_HOURS)
    read_from, read_to = window_from - pad, window_to + pad

    released: List[dict] = []
    suppressed: List[dict] = []
    held = 0
    skipped_devices = 0

    names = devices if devices is not None else sorted(data_store.device_data_dict.keys())
    for device in names:
        series = _series_for(device)
        if len(series) < 10:
            skipped_devices += 1
            continue
        context = [
            p for p in series
            if read_from <= (_parse_ts(p.get("time")) or datetime.min) <= read_to
        ]
        if len(context) < 10:
            skipped_devices += 1
            continue

        imei = str(context[-1].get("imei") or "")
        try:
            events = detect_fill_theft(device, context)
        except Exception as e:
            print(f"[alerts] slab {device}: detection failed: {e}")
            continue

        for ev in events:
            start = _parse_ts(ev.get("eventTime"))
            if start is None or not (window_from <= start < window_to):
                continue  # context only -- not in the slab that was asked for

            row = dict(ev)
            row["deviceName"] = device
            row["imei"] = imei

            # Kept rather than dropped: on an inspection screen a near miss is worth
            # seeing, so it lands in Filtered with the reason instead of vanishing.
            if DECISION_RANK.get(str(ev.get("decision")), 0) < min_rank:
                row["suppressedReason"] = f"below_{config.ALERT_MIN_DECISION}"
                suppressed.append(row)
                continue

            status, reason = confirm_event(device, imei, ev, series, now=now,
                                           require_reporting=False)
            if status == "hold":
                held += 1
                continue
            if status == "suppress":
                row["suppressedReason"] = reason
                suppressed.append(row)
                continue
            released.append(row)

    digest = _build_digest(now, released, suppressed, held, skipped_devices,
                           len(names), time.time() - started)
    digest["slab"] = {
        "from": window_from.isoformat(timespec="seconds"),
        "to": window_to.isoformat(timespec="seconds"),
        "minutes": round((window_to - window_from).total_seconds() / 60.0, 1),
    }
    print(f"[alerts] slab {window_from:%Y-%m-%d %H:%M}-{window_to:%H:%M}: "
          f"{digest['theftCount']} theft / {digest['fillCount']} fill, "
          f"{held} held, {len(suppressed)} filtered in {digest['tookSeconds']}s")
    return digest


def latest_digest() -> Optional[dict]:
    if _last_digest is not None:
        return _last_digest
    if LATEST_PATH.exists():
        try:
            return json.loads(LATEST_PATH.read_text())
        except (OSError, ValueError):
            pass
    return None


# ------------------------------------------------------------------- background job

def start_alert_agent(interval_sec: Optional[float] = None):
    global _bg_thread
    if _bg_thread is not None and _bg_thread.is_alive():
        return _bg_thread
    interval = float(interval_sec if interval_sec is not None else config.ALERT_INTERVAL_SECONDS)
    if interval <= 0:
        return None

    def _loop():
        # Let the first live refresh land before the first scan.
        time.sleep(60)
        while True:
            started = time.time()
            try:
                run_cycle()
            except Exception as e:
                print(f"[alerts] cycle failed: {e}")
            # Steady cadence regardless of how long the pass took.
            time.sleep(max(60.0, interval - (time.time() - started)))

    _bg_thread = threading.Thread(target=_loop, name="alert-agent", daemon=True)
    _bg_thread.start()
    print(f"[alerts] agent started (every {interval:.0f}s, "
          f"settle {config.ALERT_SETTLE_MINUTES:.0f}min, min={config.ALERT_MIN_DECISION})")
    return _bg_thread
