"""Per-vehicle / per-day summary tables with safe rollups + CSV/HTML export."""

from __future__ import annotations

import html
import os
from pathlib import Path
from typing import Optional, Tuple

import numpy as np
import pandas as pd

import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config


def safe_avg_consumption(total_consumption: float, running_time_hours: float):
    if running_time_hours is None or running_time_hours <= 0:
        return None
    if total_consumption is None:
        return None
    return float(total_consumption) / float(running_time_hours)


def safe_mileage(distance: float, consumption: float):
    if consumption is None or consumption <= 0:
        return None
    if distance is None or distance < 0:
        return None
    return float(distance) / float(consumption)


def _day_key(ts) -> str:
    return pd.Timestamp(ts).tz_convert(config.TIMEZONE).strftime("%Y-%m-%d") if getattr(pd.Timestamp(ts), "tzinfo", None) or getattr(pd.Timestamp(ts), "tz", None) else pd.Timestamp(ts).strftime("%Y-%m-%d")


def build_event_table(events: pd.DataFrame) -> pd.DataFrame:
    if events is None or events.empty:
        return pd.DataFrame(
            columns=[
                "date",
                "vehicle_name",
                "imei",
                "event_type",
                "quantity_liters",
                "timestamp",
                "confidence",
                "decision",
                "tamper_flag",
                "recurrence_group",
                "soft_alert",
            ]
        )

    out = events.copy()
    out["date"] = out["timestamp"].apply(_day_key)
    cols = [
        "date",
        "vehicle_name",
        "imei",
        "event_type",
        "quantity_liters",
        "timestamp",
        "confidence",
        "decision",
        "tamper_flag",
        "recurrence_group",
        "channel_disagreement",
        "low_internal_voltage",
        "truly_parked",
        "ignition",
        "soft_alert",
        "discarded_as_glitch",
        "discard_reason",
    ]
    for c in cols:
        if c not in out.columns:
            out[c] = None
    return out[cols].sort_values(["date", "vehicle_name", "timestamp"])


def compute_day_rollups(prepared: pd.DataFrame, events: pd.DataFrame) -> pd.DataFrame:
    """Safe per-vehicle/day consumption / mileage rollups + soft-alert cross-check."""
    if prepared is None or prepared.empty:
        return pd.DataFrame()

    df = prepared.copy()
    df["date"] = df["timestamp"].apply(_day_key)
    fuel_col = "fuel_used" if "fuel_used" in df.columns else (
        "fuel_smooth" if "fuel_smooth" in df.columns else "fuel_level"
    )

    rows = []
    for (imei, date), g in df.groupby(["imei", "date"]):
        g = g.sort_values("timestamp")
        vehicle = g["vehicle_name"].dropna().iloc[0] if "vehicle_name" in g and g["vehicle_name"].notna().any() else None
        fuel = pd.to_numeric(g[fuel_col], errors="coerce")
        # Consumption ≈ sum of negative actual changes (clamped)
        actual = pd.to_numeric(g.get("actual_change"), errors="coerce").fillna(0.0)
        total_consumption = float((-actual.clip(upper=0)).sum())
        total_fill_from_series = float(actual.clip(lower=0).sum())
        running_hours = float(
            g.loc[g["ignition"].astype(str).str.lower().eq("on"), "engine_hours_delta"]
            .fillna(0)
            .sum()
        )
        distance = float(
            g.loc[g["movement"].astype(bool), "distance_km"].fillna(0).sum()
            if "movement" in g.columns
            else g["distance_km"].fillna(0).sum()
        )

        ev = events
        if ev is not None and not ev.empty:
            ev = ev.copy()
            ev["date"] = ev["timestamp"].apply(_day_key)
            day_ev = ev[(ev["imei"].astype(str) == str(imei)) & (ev["date"] == date)]
            day_ev = day_ev[day_ev.get("decision", "") != "LIKELY_NOISE"] if "decision" in day_ev else day_ev
        else:
            day_ev = pd.DataFrame()

        fill_qty = float(day_ev.loc[day_ev["event_type"] == "FILL", "quantity_liters"].sum()) if not day_ev.empty else 0.0
        drain_qty = float(day_ev.loc[day_ev["event_type"] == "DRAIN", "quantity_liters"].sum()) if not day_ev.empty else 0.0
        soft_count = int(g["soft_alert"].notna().sum()) if "soft_alert" in g.columns else 0
        detector_count = int(len(day_ev)) if not day_ev.empty else 0
        disagreement = None
        if soft_count > 0:
            disagreement = abs(detector_count - soft_count) >= max(2, int(0.5 * soft_count))

        rows.append(
            {
                "date": date,
                "vehicle_name": vehicle,
                "imei": imei,
                "points": len(g),
                "total_consumption_l": round(total_consumption, 3),
                "running_time_hours": round(running_hours, 3),
                "distance_km": round(distance, 3),
                "avg_consumption_lph": safe_avg_consumption(total_consumption, running_hours),
                "mileage_kmpl": safe_mileage(distance, total_consumption),
                "detected_fill_l": round(fill_qty, 3),
                "detected_drain_l": round(drain_qty, 3),
                "series_positive_delta_l": round(total_fill_from_series, 3),
                "soft_alert_count": soft_count,
                "detector_event_count": detector_count,
                "soft_alert_disagreement": disagreement,
            }
        )

    out = pd.DataFrame(rows)
    # Ensure None stays None (not NaN→inf)
    for col in ("avg_consumption_lph", "mileage_kmpl"):
        if col in out.columns:
            out[col] = out[col].apply(lambda x: None if x is None or (isinstance(x, float) and (np.isnan(x) or np.isinf(x))) else x)
    return out


def export_reports(
    events: pd.DataFrame,
    rollups: pd.DataFrame,
    *,
    stem: str = "fuel_events",
    out_dir: Optional[str] = None,
) -> Tuple[str, str]:
    out_dir = Path(out_dir or (Path(config.OUTPUT_DIR) / "reports"))
    out_dir.mkdir(parents=True, exist_ok=True)

    event_table = build_event_table(events)
    csv_path = out_dir / f"{stem}_events.csv"
    html_path = out_dir / f"{stem}_report.html"
    rollup_csv = out_dir / f"{stem}_daily_rollups.csv"

    event_table.to_csv(csv_path, index=False)
    if rollups is not None and not rollups.empty:
        rollups.to_csv(rollup_csv, index=False)

    html_doc = _render_html(event_table, rollups, title=stem)
    html_path.write_text(html_doc, encoding="utf-8")
    print(f"[report] wrote {csv_path}")
    print(f"[report] wrote {html_path}")
    return str(csv_path), str(html_path)


def _render_html(events: pd.DataFrame, rollups: pd.DataFrame, title: str) -> str:
    def table(df: pd.DataFrame) -> str:
        if df is None or df.empty:
            return "<p><em>No rows</em></p>"
        cols = list(df.columns)
        head = "".join(f"<th>{html.escape(str(c))}</th>" for c in cols)
        body_rows = []
        for _, row in df.iterrows():
            tds = [].copy()
            for c in cols:
                val = row[c]
                if val is None or (isinstance(val, float) and np.isnan(val)):
                    text = ""
                else:
                    text = html.escape(str(val))
                tds.append(f"<td>{text}</td>")
            body_rows.append("<tr>" + "".join(tds) + "</tr>")
        return f"<table><thead><tr>{head}</tr></thead><tbody>{''.join(body_rows)}</tbody></table>"

    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"/>
<title>{html.escape(title)}</title>
<style>
body {{ font-family: Segoe UI, sans-serif; margin: 24px; color: #111; }}
h1,h2 {{ margin-top: 1.4em; }}
table {{ border-collapse: collapse; width: 100%; font-size: 13px; }}
th, td {{ border: 1px solid #ddd; padding: 6px 8px; text-align: left; }}
th {{ background: #f3f4f6; }}
tr:nth-child(even) {{ background: #fafafa; }}
.badge-drain {{ color: #b91c1c; font-weight: 600; }}
.badge-fill {{ color: #15803d; font-weight: 600; }}
</style></head>
<body>
<h1>Fuel Theft &amp; Fill Report</h1>
<p>Generated report stem: <code>{html.escape(title)}</code></p>
<h2>Daily rollups</h2>
{table(rollups)}
<h2>Detected events</h2>
{table(events)}
</body></html>
"""
