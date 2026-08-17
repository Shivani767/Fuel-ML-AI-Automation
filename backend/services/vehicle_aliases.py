"""Map spreadsheet / short API names → Fuelark display deviceName values.

Display names come from Fuelark Vehicle Summary (see fuel_data/device_display_names.json).
Excel IMEIs like 3.53E+14 are corrupted — never use them for matching.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, Optional

from backend import config

# Spreadsheet / verified case name → short or display deviceName
NAME_OVERRIDES = {
    "Kamdhenu FBD TM HR-55-P-8009": "Kamdhenu FBD",
    "JSPL SMS Slag CG-13-AW-1214": "JSPL SMS",
    "Gawar Mum HR-39-E-4702": "Gawar Mum",
    "Gawar Mum HR-39-E-5960": "Gawar Mum",
    "Gawar Mum EX-45 Excavator": "Gawar Mum Excavator",
    "Gawar Mum Excavator EX-76/YC06538": "Gawar Mum Excavator",
    "Gawar Mum Hydra MH-46-CV-4414": "Gawar Mum Lift",
    "Gawar Mum Hydra MH-46-CV-3406": "Gawar Mum Lift",
    "Gawar Mum Paver No 20 (1800-3)": "Gawar Mum",
    "Ceigall LBG TM PB-10-HH-7081": "Ceigall LBG TM",
    "Ceigall LBG TM PB-10-HJ-2981": "Ceigall LBG TM",
    "Ceigall LBG TM HR-65-B-2189": "Ceigall LBG TM",
    "eigall LBG TM HR-65-B-2189": "Ceigall LBG TM",
    "Ceigall LBG TIP HR-65-A-9190": "Ceigall LBG TIP",
    " Ceigall LBG TIP HR-65-A-9190": "Ceigall LBG TIP",
    "Ceigall LBG TIP PB-10-JT-7834": "Ceigall LBG TIP",
    "Ceigall LBG TIP PB-10-JX-0618": "Ceigall LBG TIP",
    "Krati Tipper MP-45-ZC-8873": "Krati Tipper",
    "Krati Tipper RJ-21-GA-9016": "Krati Tipper",
    "Kamdhenu Sohana HR-55-AQ-7995 TM": "Kamdhenu Sohana",
    "Kamdhenu Sohana HR-55-AQ-7995 TM ": "Kamdhenu Sohana",
    "RJ-14-GR-2657 Ananta TIP": "RJ-14-GP-7755 Ananta Tipper",
    "RJ-14-GR-2655 Ananta Tipper": "RJ-14-GP-7755 Ananta Tipper",
    "RJ-14-GR-2656 Ananta Tipper": "RJ-14-GP-7755 Ananta Tipper",
    "Shiva Hyva RJ-14-GR-2593": "Shiva DG-160Kva",
    "Shiva Hyva RJ 14 GQ 5746": "Shiva DG-160Kva",
    "Shiva": "Shiva DG-160Kva",
    "RoyalGen DG-62Kva HR-55-AN-8593": "RoyalGen DG-62Kva HR-55-AS-3525",
    "JSPL DRI-1 CG-13-AF-5067": "JSPL Batching",
    "Gawar HIM EX-43 SR-YQ12178": "Gawar HIM EX-65",
    "Gawar HIM EX-75 SR-YQ13720": "Gawar HIM EX-65",
    "Gawar HIM L-05 HR-20-AH-7350": "Gawar HIM EX-65",
    "Gawar HIM PTR Roller-26": "Gawar HIM EX-65",
    "RKC/TIPPER/103 HR-68-C-3144": "RKC/TIPPER/126",
    "Skylark Banglore DG51 DG-125kva (BOYD)": "Skylark Banglore TRT05",
    "Skylark Surat HC09 NL-01-DA-0138 (BOYD)": "Skylark Surat DG19",
    "SKS Raebareli Transit Mixer UP-83-AT-7394": "SKS Raebareli Trailer",
    "SKS Raebareli Transit Mixer UP-83-AT-7394 ": "SKS Raebareli Trailer",
    "Rise-11-Agra Tractor HR-51-CN-2046": "Rise-11-GGN",
    "Excavator-210 Ananta Sany": "SK-220 Ananta Excavator Kob",
}

_DISPLAY_PATH = config.FUEL_DATA_DIR / "device_display_names.json"
_short_to_display: Dict[str, str] = {}
_imei_to_display: Dict[str, str] = {}
_display_to_short: Dict[str, str] = {}
_loaded = False


def _load_display_maps() -> None:
    global _loaded, _short_to_display, _imei_to_display, _display_to_short
    if _loaded:
        return
    path = Path(_DISPLAY_PATH)
    if path.exists():
        with open(path, encoding="utf-8") as f:
            raw = json.load(f)
        _short_to_display = {
            str(k).strip(): str(v).strip()
            for k, v in (raw.get("short_to_display") or {}).items()
        }
        _imei_to_display = {
            str(k).strip(): str(v).strip()
            for k, v in (raw.get("imei_to_display") or {}).items()
        }
        _display_to_short = {v: k for k, v in _short_to_display.items()}
    _loaded = True


def short_to_display() -> Dict[str, str]:
    _load_display_maps()
    return _short_to_display


def imei_to_display() -> Dict[str, str]:
    _load_display_maps()
    return _imei_to_display


def to_display_name(name: str, imei: Optional[str] = None) -> str:
    """Canonical Fuelark display name for a short API name and/or IMEI."""
    _load_display_maps()
    if imei:
        mapped = _imei_to_display.get(str(imei).strip())
        if mapped:
            return mapped
    raw = (name or "").strip()
    if not raw:
        return raw
    return _short_to_display.get(raw, raw)


def spreadsheet_to_device(name: str) -> str:
    """Return canonical deviceName (Fuelark display name when known)."""
    _load_display_maps()
    raw = (name or "").strip()
    if not raw:
        return raw
    mapped = raw
    if raw in NAME_OVERRIDES:
        mapped = NAME_OVERRIDES[raw]
    elif raw + " " in NAME_OVERRIDES:
        mapped = NAME_OVERRIDES[raw + " "]
    elif " " + raw in NAME_OVERRIDES:
        mapped = NAME_OVERRIDES[" " + raw]
    # Prefer Fuelark Vehicle Summary display name
    return _short_to_display.get(mapped, mapped)


def names_for_device(device_name: str) -> set:
    """All spreadsheet / short / display names that refer to this device."""
    _load_display_maps()
    dn = (device_name or "").strip()
    out = {dn} if dn else set()
    # Include short form if this is a display name
    short = _display_to_short.get(dn)
    if short:
        out.add(short)
    # Include display form if this is a short name
    display = _short_to_display.get(dn)
    if display:
        out.add(display)
    dn_l = dn.lower()
    for sheet, mapped in NAME_OVERRIDES.items():
        canon = _short_to_display.get(mapped, mapped)
        if mapped.strip().lower() == dn_l or canon.strip().lower() == dn_l:
            out.add(sheet.strip())
            out.add(mapped.strip())
            out.add(canon.strip())
    return {x for x in out if x}
