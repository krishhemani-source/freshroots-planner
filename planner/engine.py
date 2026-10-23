"""Runs the rule engine over every SKU in the export files."""

from datetime import date

import pandas as pd

from planner.config import Config
from planner.metrics import distributor_changes, sku_metrics, weekly_sales_matrix
from planner.rules import decide_sku

TOP_DISTRIBUTORS_SHOWN = 5


def run_rule_engine(frames: dict[str, pd.DataFrame], run_date: date, cfg: Config) -> pd.DataFrame:
    """One row per SKU: metrics, flags, recommended qty, track, reasons."""
    master = frames["sku_master"].set_index("sku_id")
    sales = frames["sales_weekly"]
    weekly = weekly_sales_matrix(sales).reindex(master.index, fill_value=0)

    # weeks of history = weeks from the SKU's first sale to the last complete week
    first_sale = (weekly > 0).to_numpy().argmax(axis=1)
    has_sales = (weekly > 0).to_numpy().any(axis=1)
    weeks_of_history = pd.Series(
        [weekly.shape[1] - f if ok else 0 for f, ok in zip(first_sale, has_sales)], index=weekly.index)

    on_hand = frames["stock_on_hand"].groupby("sku_id")["on_hand_units"].sum()
    pos = frames["zoho_open_pos"]
    on_order = pos[pos["status"].str.lower() != "closed"].groupby("sku_id")["qty_ordered"].sum()

    dist = distributor_changes(sales, cfg)
    anomalies = {sku: g.drop(columns=["sku_id"]).to_dict("records")
                 for sku, g in dist[dist["anomaly"]].groupby("sku_id")}

    # for the context pack: each SKU's top distributors (plus any anomalous ones), with city and tier
    places = frames["distributors"].set_index("distributor_id")[["city", "tier"]]
    shown = dist[(dist["rank"] <= TOP_DISTRIBUTORS_SHOWN) | dist["anomaly"]].join(places, on="distributor_id")
    top_distributors = {sku: g.sort_values("rank")[["distributor_id", "city", "tier", "avg_12w", "avg_recent",
                                                     "change_pct", "anomaly"]].to_dict("records")
                        for sku, g in shown.groupby("sku_id")}
    trend_weeks = cfg.rules["history_weeks_long"]

    rows = []
    for sku_id, sku in master.iterrows():
        std_qty = int(sku["standard_order_qty"])
        m = sku_metrics(weekly.loc[sku_id].to_numpy(), int(weeks_of_history[sku_id]),
                        float(on_hand.get(sku_id, 0)), float(on_order.get(sku_id, 0)), run_date.month, cfg)
        d = decide_sku(m, sku["perishability"], std_qty, anomalies.get(sku_id, []), cfg)
        rows.append({
            "sku_id": sku_id, "name": sku["name"], "category": sku["category"],
            "perishability": sku["perishability"], "standard_order_qty": std_qty,
            "unit_cost_inr": float(sku["unit_cost_inr"]),
            **m, **d, "anomalies": anomalies.get(sku_id, []),
            "trend_12w": weekly.loc[sku_id].iloc[-trend_weeks:].astype(int).tolist(),
            "trend_weeks": list(weekly.columns[-trend_weeks:]),
            "top_distributors": top_distributors.get(sku_id, []),
        })
    return pd.DataFrame(rows)
