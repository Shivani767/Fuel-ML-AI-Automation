"""Fuel-level graph with detected events annotated (Plotly HTML + PNG)."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

import pandas as pd

import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config


def _decision_opacity(decision: str) -> float:
    if decision == "AUTO_CONFIRMED":
        return 0.35
    if decision == "MANUAL_REVIEW":
        return 0.22
    return 0.10


def plot_events(
    prepared: pd.DataFrame,
    events: pd.DataFrame,
    *,
    title: Optional[str] = None,
    out_html: Optional[str] = None,
    out_png: Optional[str] = None,
    show_raw: bool = True,
) -> str:
    """
    Build an interactive Plotly chart. Returns path to HTML file.
    Also writes PNG when kaleido/matplotlib fallback is available.
    """
    import plotly.graph_objects as go
    from plotly.subplots import make_subplots

    if prepared is None or prepared.empty:
        raise ValueError("No telemetry to plot")

    df = prepared.sort_values("timestamp").reset_index(drop=True)
    imei = str(df["imei"].iloc[0])
    vehicle = df["vehicle_name"].dropna().iloc[0] if "vehicle_name" in df and df["vehicle_name"].notna().any() else imei
    title = title or f"Fuel events — {vehicle} ({imei})"

    fig = go.Figure()

    # Ignition-off background bands
    ign = df["ignition"].astype(str).str.lower().isin(["off", "0", "false"])
    if ign.any():
        band_start = None
        times = df["timestamp"].tolist()
        flags = ign.tolist()
        for i, off in enumerate(flags):
            if off and band_start is None:
                band_start = times[i]
            if (not off or i == len(flags) - 1) and band_start is not None:
                band_end = times[i] if not off else times[i]
                fig.add_vrect(
                    x0=band_start,
                    x1=band_end,
                    fillcolor="rgba(160,160,160,0.18)",
                    line_width=0,
                    layer="below",
                )
                band_start = None

    if show_raw and "fuel_level" in df.columns:
        fig.add_trace(
            go.Scatter(
                x=df["timestamp"],
                y=df["fuel_level"],
                name="Raw fuel",
                mode="lines",
                line=dict(color="rgba(100,149,237,0.35)", width=1),
            )
        )
    if show_raw and "edc_smooth" in df.columns:
        fig.add_trace(
            go.Scatter(
                x=df["timestamp"],
                y=df["edc_smooth"],
                name="EDC (scaled)",
                mode="lines",
                line=dict(color="rgba(46,139,87,0.35)", width=1),
            )
        )

    fuel_col = "fuel_used" if "fuel_used" in df.columns else (
        "fuel_smooth" if "fuel_smooth" in df.columns else "fuel_level"
    )
    fig.add_trace(
        go.Scatter(
            x=df["timestamp"],
            y=df[fuel_col],
            name="Smoothed fuel",
            mode="lines",
            line=dict(color="#1d4ed8", width=2.5),
        )
    )

    if events is not None and not events.empty:
        for _, ev in events.iterrows():
            if ev.get("decision") == "LIKELY_NOISE" and ev.get("discarded_as_glitch"):
                continue
            etype = str(ev.get("event_type", "")).upper()
            color = "#dc2626" if etype == "DRAIN" else "#16a34a"
            ts = pd.Timestamp(ev["timestamp"])
            qty = float(ev.get("quantity_liters") or 0)
            conf = float(ev.get("confidence") or 0)
            decision = str(ev.get("decision") or "")
            # nearest fuel for marker y
            idx = (df["timestamp"] - ts).abs().idxmin()
            y = float(df.loc[idx, fuel_col])
            fig.add_vline(
                x=ts,
                line=dict(color=color, width=2, dash="dash"),
                opacity=_decision_opacity(decision) + 0.4,
            )
            fig.add_trace(
                go.Scatter(
                    x=[ts],
                    y=[y],
                    mode="markers+text",
                    name=f"{etype} {qty:.1f}L",
                    text=[f"{etype}<br>{qty:.1f}L<br>{decision}<br>c={conf:.2f}"],
                    textposition="top center",
                    marker=dict(
                        size=12 + min(conf, 1.0) * 8,
                        color=color,
                        symbol="triangle-down" if etype == "DRAIN" else "triangle-up",
                        line=dict(width=1, color="#111"),
                    ),
                    showlegend=True,
                )
            )
            if bool(ev.get("tamper_flag")):
                fig.add_trace(
                    go.Scatter(
                        x=[ts],
                        y=[y],
                        mode="markers",
                        name="Tamper",
                        marker=dict(
                            size=16,
                            color="#f59e0b",
                            symbol="star",
                            line=dict(width=1, color="#92400e"),
                        ),
                        showlegend=True,
                    )
                )

    fig.update_layout(
        title=title,
        xaxis_title="Time",
        yaxis_title="Fuel (L)",
        template="plotly_white",
        hovermode="x unified",
        legend=dict(orientation="h", yanchor="bottom", y=1.02),
        height=640,
    )

    out_dir = Path(config.OUTPUT_DIR) / "plots"
    out_dir.mkdir(parents=True, exist_ok=True)
    if out_html is None:
        safe = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in str(vehicle))[:60]
        out_html = str(out_dir / f"{safe}_{imei}_events.html")

    fig.write_html(out_html, include_plotlyjs="cdn")

    if out_png is None:
        out_png = out_html.replace(".html", ".png")
    try:
        fig.write_image(out_png, scale=2)
    except Exception:
        # Fallback static PNG via matplotlib (no kaleido required)
        try:
            _matplotlib_fallback(df, events, fuel_col, title, out_png)
        except Exception as exc:
            print(f"[plot] PNG export skipped: {exc}")

    print(f"[plot] wrote {out_html}")
    return out_html


def _matplotlib_fallback(df, events, fuel_col, title, out_png):
    import matplotlib.pyplot as plt
    import matplotlib.dates as mdates

    fig, ax = plt.subplots(figsize=(14, 6))
    ax.plot(df["timestamp"], df[fuel_col], color="#1d4ed8", lw=2, label="Smoothed fuel")
    if events is not None and not events.empty:
        for _, ev in events.iterrows():
            if ev.get("discarded_as_glitch"):
                continue
            etype = str(ev.get("event_type", "")).upper()
            color = "#dc2626" if etype == "DRAIN" else "#16a34a"
            ts = pd.Timestamp(ev["timestamp"])
            idx = (df["timestamp"] - ts).abs().idxmin()
            y = float(df.loc[idx, fuel_col])
            ax.axvline(ts, color=color, ls="--", alpha=0.6)
            ax.scatter([ts], [y], c=color, s=60, zorder=5)
            ax.annotate(
                f"{etype} {float(ev.get('quantity_liters') or 0):.1f}L",
                (ts, y),
                textcoords="offset points",
                xytext=(0, 12),
                ha="center",
                fontsize=8,
                color=color,
            )
            if bool(ev.get("tamper_flag")):
                ax.scatter([ts], [y], marker="*", c="#f59e0b", s=160, zorder=6)
    ax.set_title(title)
    ax.set_ylabel("Fuel (L)")
    ax.set_xlabel("Time")
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%m-%d %H:%M"))
    fig.autofmt_xdate()
    ax.grid(True, alpha=0.3)
    fig.tight_layout()
    fig.savefig(out_png, dpi=140)
    plt.close(fig)
    print(f"[plot] wrote {out_png}")
