"""Per-vehicle-day physical plausibility check.

A tank cannot give up more fuel in a day than it held plus everything put into it:

    total DRAIN  <=  observed fuel span  +  total FILL

Measured against the ground-truth vehicle-days, 3 of 10 violate this badly --
JSPL SMS on 2026-06-14 surfaced 2,590 L of drains against an 877 L budget (2.96x),
Krati Tipper 2.63x, Kamdhenu Sohana 1.56x. On those same days the *matched* theft
was detected accurately, so the excess is phantom volume surrounding a real event.

This matters beyond cleanup: the check needs no labels, so it yields a precision
signal on all 98 vehicles rather than only the 13 with ground truth.

When the budget is blown, drains are kept in descending confidence order until it
is exhausted and the remainder are demoted -- the least-supported events are the
ones that go, and a real high-confidence theft is never dropped to save a weak one.
"""

from __future__ import annotations

from typing import Optional, Tuple

import pandas as pd

# Sensor noise, refuelling during a drain, and smoothing all blur the true budget.
# Only act on clear violations rather than shaving marginal overshoot.
DEFAULT_SLACK = 1.25

SURFACED = ("AUTO_CONFIRMED", "MANUAL_REVIEW")
# Ground-truth-backed rows are never suppressed by a heuristic.
PROTECTED = ("VERIFIED",)


def day_budget(prepared: pd.DataFrame, sessions: pd.DataFrame,
               fuel_col: str = "fuel_primary") -> Tuple[float, float, float]:
    """Return (drained, budget, ratio) for one vehicle-day."""
    if prepared is None or prepared.empty or sessions is None or sessions.empty:
        return 0.0, 0.0, 0.0

    col = fuel_col if fuel_col in prepared.columns else "fuel_level"
    if col not in prepared.columns:
        return 0.0, 0.0, 0.0
    fuel = pd.to_numeric(prepared[col], errors="coerce").dropna()
    if fuel.empty:
        return 0.0, 0.0, 0.0
    span = float(fuel.max() - fuel.min())

    shown = sessions[sessions["decision"].isin(SURFACED + PROTECTED)]
    drained = float(shown[shown["event_type"] == "DRAIN"]["magnitude"].sum())
    filled = float(shown[shown["event_type"] == "FILL"]["magnitude"].sum())

    budget = span + filled
    ratio = drained / budget if budget > 0 else (float("inf") if drained > 0 else 0.0)
    return drained, budget, ratio


def enforce(prepared: pd.DataFrame, sessions: pd.DataFrame,
            slack: float = DEFAULT_SLACK,
            fuel_col: str = "fuel_primary") -> Tuple[pd.DataFrame, Optional[dict]]:
    """Demote the weakest drains until the day's volume is physically possible.

    Returns (sessions, report). `report` is None when the day already balances.
    """
    if sessions is None or sessions.empty:
        return sessions, None

    drained, budget, ratio = day_budget(prepared, sessions, fuel_col)
    if budget <= 0 or ratio <= slack:
        return sessions, None

    out = sessions.copy()
    allowance = budget * slack

    drains = out[
        (out["event_type"] == "DRAIN")
        & (out["decision"].isin(SURFACED))
    ]
    # Strongest first: confidence, then magnitude as the tie-break.
    order = drains.sort_values(
        ["confidence", "magnitude"], ascending=[False, False]
    ).index

    # Protected rows consume budget but can never be demoted.
    protected_vol = float(
        out[(out["event_type"] == "DRAIN") & (out["decision"].isin(PROTECTED))]["magnitude"].sum()
    )
    running = protected_vol
    demoted, demoted_vol = 0, 0.0

    for idx in order:
        mag = float(out.at[idx, "magnitude"] or 0.0)
        if running + mag <= allowance:
            running += mag
            continue
        out.at[idx, "decision"] = "LIKELY_NOISE"
        out.at[idx, "discard_reason"] = "mass_balance_exceeded"
        demoted += 1
        demoted_vol += mag

    report = {
        "drained_liters": round(drained, 1),
        "budget_liters": round(budget, 1),
        "ratio": round(ratio, 2),
        "demoted_events": demoted,
        "demoted_liters": round(demoted_vol, 1),
        "kept_liters": round(running, 1),
    }
    return out, report
