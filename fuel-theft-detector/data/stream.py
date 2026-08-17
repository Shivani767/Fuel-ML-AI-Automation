"""Incremental reader for the large telemetry dumps.

The June/May dumps are 2.4-2.7 GB JSON arrays. `json.load()` on one of those
materialises every record as a dict at once, which costs many GB of RAM and
swaps on a laptop. Evaluation only ever needs a handful of IMEIs, so stream the
array and keep just the records asked for.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Callable, Iterator, Optional

_DECODER = json.JSONDecoder()
_CHUNK = 8 << 20  # 8 MiB


def iter_records(path: str | Path, chunk_size: int = _CHUNK) -> Iterator[dict]:
    """Yield each object from a top-level JSON array without loading the file.

    Reads in chunks and repeatedly calls raw_decode at the current offset,
    refilling the buffer whenever a value straddles a chunk boundary.
    """
    with open(path, "r", encoding="utf-8") as f:
        buf = ""
        pos = 0
        started = False

        while True:
            # Skip whitespace and structural characters between values.
            while pos < len(buf) and buf[pos] in " \t\r\n,":
                pos += 1

            if not started:
                if pos < len(buf):
                    if buf[pos] != "[":
                        raise ValueError(f"{path}: expected a JSON array")
                    pos += 1
                    started = True
                    continue
            elif pos < len(buf) and buf[pos] == "]":
                return

            try:
                obj, end = _DECODER.raw_decode(buf, pos)
            except ValueError:
                # Incomplete value (or nothing buffered yet) -> read more.
                chunk = f.read(chunk_size)
                if not chunk:
                    return
                buf = buf[pos:] + chunk
                pos = 0
                continue

            yield obj
            pos = end

            # Drop consumed prefix so the buffer does not grow without bound.
            if pos > chunk_size:
                buf = buf[pos:]
                pos = 0


def load_by_imei_day(
    path: str | Path,
    imeis: set,
    days: Optional[set] = None,
    progress: Optional[Callable[[int], None]] = None,
) -> dict:
    """Return {(imei, 'YYYY-MM-DD'): [record, ...]} for the requested IMEIs.

    `days` optionally restricts to specific date strings, which keeps memory
    proportional to the cases under evaluation rather than to the fleet.
    """
    imeis = {str(i) for i in imeis}
    out: dict = {}
    for n, rec in enumerate(iter_records(path)):
        if progress is not None and n and n % 1_000_000 == 0:
            progress(n)
        imei = str(rec.get("imei") or "")
        if imei not in imeis:
            continue
        ts = str(rec.get("deviceDataInsertTime") or "")
        if len(ts) < 10:
            continue
        day = ts[:10]
        if days is not None and day not in days:
            continue
        out.setdefault((imei, day), []).append(rec)
    return out
