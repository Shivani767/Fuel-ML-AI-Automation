"""Minimal .env loader.

Secrets were hardcoded as defaults in fuel_api.py and tools/may_fuel_theft_report.py.
This reads key=value pairs from a .env at the repo root into os.environ so they can
live outside the tree (.env is gitignored).

Deliberately dependency-free -- python-dotenv is not installed and this needs to work
from both the repo root and from inside fuel-theft-detector/.
"""

from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent
ENV_PATH = ROOT / ".env"

_loaded = False


def load_env(path: Path | str | None = None, *, override: bool = False) -> dict:
    """Load .env into os.environ. Existing vars win unless override=True.

    Idempotent: repeated calls are cheap no-ops.
    """
    global _loaded
    target = Path(path) if path else ENV_PATH
    if _loaded and path is None and not override:
        return {}
    values: dict = {}
    if not target.exists():
        _loaded = True
        return values

    for raw in target.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip().strip('"').strip("'")
        if not key:
            continue
        values[key] = val
        if override or key not in os.environ:
            os.environ[key] = val

    if path is None:
        _loaded = True
    return values


def require(name: str, hint: str = "") -> str:
    """Fetch a required setting, failing loudly rather than falling back to a default."""
    load_env()
    val = os.environ.get(name, "")
    if not val:
        extra = f" {hint}" if hint else ""
        raise RuntimeError(
            f"{name} is not set. Add it to {ENV_PATH} (see .env.example).{extra}"
        )
    return val
