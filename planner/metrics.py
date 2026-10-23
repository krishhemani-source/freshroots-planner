"""Per-SKU numbers the rules work from. No decisions here, just arithmetic.

  avg_12w         mean weekly units over the last 12 weeks
  avg_4w          mean weekly units over the last 4 weeks
  demand_rate     avg_12w x festival uplift in festival months
  effective_stock on_hand + on_order (if include_on_order is on)
  weeks_of_cover  effective_stock / demand_rate

A SKU with less history than the window (a new launch) is averaged over the
weeks it has actually been selling, so its average is not dragged down by
weeks before it existed.
"""

import math

import numpy as np
import pandas as pd

from planner.config import Config


def sku_metrics(weekly_units: np.ndarray, weeks_of_history: int, on_hand: float, on_order: float,
                run_month: int, cfg: Config) -> dict:
    """weekly_units: this SKU's total sales per week, oldest first, ending with the last complete week."""
    r = cfg.rules
    long_w, short_w = r["history_weeks_long"], r["history_weeks_short"]
    avg_12w = float(np.sum(weekly_units[-long_w:])) / max(1, min(long_w, weeks_of_history))
    avg_4w = float(np.sum(weekly_units[-short_w:])) / max(1, min(short_w, weeks_of_history))

    festival = run_month in r["festival_months"]
    demand_rate = avg_12w * (r["festival_uplift"] if festival else 1.0)
    effective_stock = on_hand + (on_order if r["include_on_order"] else 0)
    weeks_of_cover = effective_stock / demand_rate if demand_rate > 0 else math.inf

    return {
        "avg_12w": round(avg_12w, 1),
        "avg_4w": round(avg_4w, 1),
        "festival_uplift_applied": festival,
        "demand_rate": round(demand_rate, 1),
        "on_hand": float(on_hand),
        "on_order": float(on_order),
        "effective_stock": float(effective_stock),
        "weeks_of_cover": round(weeks_of_cover, 2) if math.isfinite(weeks_of_cover) else math.inf,
        "weeks_of_history": int(weeks_of_history),
    }


def weekly_sales_matrix(sales: pd.DataFrame) -> pd.DataFrame:
    """SKU x week table of total units (weeks with no sales filled with 0)."""
    table = sales.groupby(["sku_id", "week_start"])["units_sold"].sum().unstack(fill_value=0)
    return table.reindex(columns=sorted(table.columns), fill_value=0)


def distributor_changes(sales: pd.DataFrame, cfg: Config) -> pd.DataFrame:
    """For each SKU's top distributors: last-2-week average vs own 12-week average.

    Returns one row per (sku, distributor) among the top N by 12-week volume, with
    change_pct and whether it counts as an anomaly. Distributors averaging fewer than
    min_distributor_weekly_units are ignored (their swings are noise, not signal).
    """
    rt = cfg.routing
    weeks = sorted(sales["week_start"].unique())
    long_weeks = weeks[-cfg.rules["history_weeks_long"]:]
    recent_weeks = weeks[-rt["distributor_recent_weeks"]:]
    window = sales[sales["week_start"].isin(long_weeks)]

    base = window.groupby(["sku_id", "distributor_id"])["units_sold"].sum() / len(long_weeks)
    recent = (window[window["week_start"].isin(recent_weeks)]
              .groupby(["sku_id", "distributor_id"])["units_sold"].sum() / len(recent_weeks))
    df = pd.DataFrame({"avg_12w": base, "avg_recent": recent}).fillna(0).reset_index()
    df["rank"] = df.groupby("sku_id")["avg_12w"].rank(ascending=False, method="first")
    df = df[df["rank"] <= rt["distributor_top_n"]].copy()
    df["change_pct"] = 100 * (df["avg_recent"] - df["avg_12w"]) / df["avg_12w"]
    df["anomaly"] = (df["avg_12w"] >= rt["min_distributor_weekly_units"]) & \
                    (df["change_pct"].abs() > rt["distributor_swing_pct"])
    return df.round({"avg_12w": 1, "avg_recent": 1, "change_pct": 1})
