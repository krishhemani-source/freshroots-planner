"""Step 5: a compact JSON context pack per exception SKU. This is the ONLY data ever sent to the AI.

Each pack holds:
  sku                     attributes (id, name, category, perishability, standard order, unit cost)
  sales_trend_12w         total units per week, oldest first
  rule_metrics            the numbers the rules used, the flags and the rule quantity
  exception_reasons       why the SKU is an exception
  top_distributors        top 5 by volume (plus any anomalous ones): last 2 weeks vs own 12-week average
  similar_skus            2-3 SKUs in the same category and perishability with the closest volume
  raghav_recent_decisions his last 5 decisions on the same kind of exception
"""

import math

import pandas as pd
from sqlalchemy import select

from planner.db import ReviewDecision, get_sessionmaker, to_json

SKU_FIELDS = ["sku_id", "name", "category", "perishability", "standard_order_qty", "unit_cost_inr"]
METRIC_FIELDS = ["avg_12w", "avg_4w", "demand_rate", "festival_uplift_applied", "on_hand", "on_order",
                 "effective_stock", "weeks_of_cover", "weeks_of_history", "flags", "candidate_qty",
                 "recommended_qty", "cover_min", "cover_max", "cover_target"]
SIMILAR_FIELDS = ["sku_id", "name", "avg_12w", "avg_4w", "weeks_of_cover", "rule_fired"]
N_SIMILAR, N_PAST = 3, 5


def exception_types(reasons: list[str]) -> list[str]:
    return [r.split(":")[0] for r in reasons]


def similar_skus(decision: dict, all_decisions: pd.DataFrame, n: int = N_SIMILAR) -> list[dict]:
    """Same category and perishability, closest in weekly volume, with full history."""
    pool = all_decisions[(all_decisions["category"] == decision["category"])
                         & (all_decisions["perishability"] == decision["perishability"])
                         & (all_decisions["sku_id"] != decision["sku_id"])
                         & (all_decisions["weeks_of_history"] >= 12)]
    if pool.empty:
        return []
    target = math.log(max(decision["avg_12w"], 1))
    distance = (pool["avg_12w"].clip(lower=1).map(math.log) - target).abs()
    return pool.loc[distance.nsmallest(n).index, SIMILAR_FIELDS].to_dict("records")


def relevant_past_decisions(decision: dict, past: list[dict], n: int = N_PAST) -> list[dict]:
    """Raghav's latest decisions on the same kind of exception, same category first."""
    types = set(exception_types(decision["exception_reasons"]))
    matches = [p for p in past if types & set(p["exception_types"])]
    matches.sort(key=lambda p: (p["category"] == decision["category"], p["decided_at"]), reverse=True)
    return [{"date": str(p["decided_at"].date()), "category": p["category"], "exception_types": p["exception_types"],
             "rule_qty": p["rule_qty"], "ai_range": [p["ai_qty_min"], p["ai_qty_max"]], "action": p["action"],
             "final_qty": p["final_qty"], "reason": p["reason"]} for p in matches[:n]]


def build_context_pack(decision: dict, similar: list[dict], past: list[dict]) -> dict:
    """decision: one row of the rule engine output (as a dict)."""
    metrics = {k: decision[k] for k in METRIC_FIELDS}
    qty = metrics["recommended_qty"]
    metrics["recommended_qty"] = None if qty is None or pd.isna(qty) else int(qty)   # None = conflicting flags
    return to_json({
        "sku": {k: decision[k] for k in SKU_FIELDS},
        "sales_trend_12w": {"first_week": (decision.get("trend_weeks") or [None])[0],
                            "weekly_units_oldest_first": decision.get("trend_12w", [])},
        "rule_metrics": metrics,
        "exception_reasons": list(decision["exception_reasons"]),
        "top_distributors": decision.get("top_distributors", []),
        "similar_skus": similar,
        "raghav_recent_decisions": past,
    })


def build_context_packs(exceptions: list[dict], all_decisions: pd.DataFrame, past: list[dict]) -> list[dict]:
    return [build_context_pack(d, similar_skus(d, all_decisions), relevant_past_decisions(d, past))
            for d in exceptions]


def load_past_decisions(db_url: str | None = None, limit: int = 500) -> list[dict]:
    with get_sessionmaker(db_url)() as s:
        rows = s.scalars(select(ReviewDecision).order_by(ReviewDecision.decided_at.desc()).limit(limit)).all()
        return [{c: getattr(r, c) for c in ["sku_id", "category", "exception_types", "rule_qty", "ai_qty_min",
                                            "ai_qty_max", "action", "final_qty", "reason", "decided_at"]}
                for r in rows]
