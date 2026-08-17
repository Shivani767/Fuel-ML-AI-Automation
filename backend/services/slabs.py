"""Time slabs and series downsampling."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import List, Optional


def parse_entry_time(entry, time_key: str = "time") -> Optional[datetime]:
    try:
        return datetime.fromisoformat(entry[time_key])
    except Exception:
        return None


def aggregate_to_slabs(entries, slab_minutes: int = 30, time_key: str = "time"):
    """Collapse dense telemetry into fixed-size time slabs.

    Buckets are anchored at midnight rather than derived from the minute-of-hour.
    `ts.minute // slab * slab` ignores the hour, so it only happens to be right for
    sizes that divide 60: `?slab=90` collapsed to whole hours and `?slab=25` produced
    a ragged 10-minute tail bucket. Anchoring at the day boundary makes any size
    uniform, and reproduces the previous behaviour exactly for the documented 30.
    """
    if not entries:
        return []
    try:
        slab_minutes = min(max(1, int(slab_minutes)), 1440)
    except (TypeError, ValueError):
        slab_minutes = 30

    buckets = {}
    for entry in entries:
        ts = parse_entry_time(entry, time_key=time_key)
        if ts is None:
            continue
        day_start = ts.replace(hour=0, minute=0, second=0, microsecond=0)
        index = int((ts - day_start).total_seconds() // (slab_minutes * 60))
        slab_start = day_start + timedelta(minutes=index * slab_minutes)
        key = slab_start.isoformat(timespec="seconds")
        existing = buckets.get(key)
        if existing is None or ts >= parse_entry_time(existing, time_key=time_key):
            point = dict(entry)
            point[time_key] = key
            point["slabMinutes"] = slab_minutes
            point["slabEnd"] = (
                slab_start + timedelta(minutes=slab_minutes)
            ).isoformat(timespec="seconds")
            buckets[key] = point

    return [buckets[k] for k in sorted(buckets.keys())]


def downsample(entries: List[dict], every_n: int = 20, max_points: int = 5000) -> List[dict]:
    """Keep every Nth point, capped at max_points (always keep first/last)."""
    if not entries:
        return []
    every_n = max(1, int(every_n or 1))
    if every_n == 1 and len(entries) <= max_points:
        return entries
    sampled = entries[::every_n]
    if sampled[-1] is not entries[-1]:
        sampled.append(entries[-1])
    if len(sampled) > max_points:
        step = max(1, len(sampled) // max_points)
        sampled = sampled[::step]
        if sampled[-1] is not entries[-1]:
            sampled.append(entries[-1])
    return sampled
