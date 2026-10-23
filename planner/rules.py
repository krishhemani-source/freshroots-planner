"""Step 4: Raghav's rules. Deterministic, no AI.

apply_rules()  -> which flags fire and the recommended quantity
route()        -> Track A (rule-clear) or Track B (exception) and why
decide_sku()   -> both, plus a plain-English reason with the numbers behind it
"""

import math

from planner.config import Config

HIGH_DEMAND, STOCKOUT_RISK, OVERSTOCK, OK = "HIGH_DEMAND", "STOCKOUT_RISK", "OVERSTOCK", "OK"


def cover_limits(perishability: str, cfg: Config) -> tuple[float, float, float]:
    """(min weeks, max weeks, target weeks). Target is the midpoint of min and max."""
    key = "high_perishability" if perishability == "high" else "standard"
    lo, hi = cfg.rules["cover_weeks"][key]["min"], cfg.rules["cover_weeks"][key]["max"]
    return lo, hi, (lo + hi) / 2


def round_up_to_multiple(qty: float, multiple: int) -> int:
    return int(math.ceil(qty / multiple) * multiple) if qty > 0 else 0


def apply_rules(m: dict, perishability: str, std_qty: int, cfg: Config) -> dict:
    """Flags and recommended quantity for one SKU, given its metrics."""
    r = cfg.rules
    lo, hi, target = cover_limits(perishability, cfg)
    flags, candidate_qty = [], {}

    if m["avg_4w"] > r["high_demand_ratio"] * m["avg_12w"]:
        flags.append(HIGH_DEMAND)
        candidate_qty[HIGH_DEMAND] = math.ceil(r["high_demand_order_multiple"] * std_qty)
    if m["weeks_of_cover"] < lo:
        flags.append(STOCKOUT_RISK)
        gap = target * m["demand_rate"] - m["effective_stock"]
        candidate_qty[STOCKOUT_RISK] = round_up_to_multiple(gap, std_qty)
    if m["weeks_of_cover"] > hi:
        flags.append(OVERSTOCK)
        candidate_qty[OVERSTOCK] = 0

    conflict = HIGH_DEMAND in flags and OVERSTOCK in flags
    if conflict:
        qty = None                      # rules disagree: Raghav decides
    elif flags:
        qty = max(candidate_qty.values())
    else:
        qty = 0

    return {
        "flags": flags or [OK],
        "conflict": conflict,
        "candidate_qty": candidate_qty,
        "recommended_qty": qty,
        "cover_min": lo, "cover_max": hi, "cover_target": target,
    }


def route(m: dict, rules_out: dict, std_qty: int, anomalies: list[dict], cfg: Config) -> list[str]:
    """Exception reasons. Empty list = Track A; anything else = Track B."""
    rt = cfg.routing
    reasons = []
    if m["weeks_of_history"] < rt["new_sku_min_weeks"]:
        reasons.append(f"NEW_SKU: only {m['weeks_of_history']} weeks of sales history "
                       f"(needs {rt['new_sku_min_weeks']})")
    if rules_out["conflict"]:
        reasons.append("CONFLICTING_FLAGS: " + " and ".join(rules_out["flags"]))
    if anomalies:
        worst = max(anomalies, key=lambda a: abs(a["change_pct"]))
        reasons.append(f"DISTRIBUTOR_ANOMALY: {len(anomalies)} top distributor(s) moved more than "
                       f"±{rt['distributor_swing_pct']}% (largest: {worst['distributor_id']} "
                       f"{worst['change_pct']:+.0f}%)")
    if m["avg_12w"] > 0:
        swing = 100 * (m["avg_4w"] - m["avg_12w"]) / m["avg_12w"]
        if abs(swing) > rt["sudden_swing_pct"]:
            reasons.append(f"SUDDEN_SWING: avg_4w is {swing:+.0f}% vs avg_12w (limit ±{rt['sudden_swing_pct']}%)")
    qty = rules_out["recommended_qty"]
    if qty is not None and qty > rt["max_qty_multiple"] * std_qty:
        reasons.append(f"QTY_ABOVE_LIMIT: {qty:,} is more than {rt['max_qty_multiple']} x "
                       f"standard order ({std_qty:,})")
    return reasons


def explain(m: dict, rules_out: dict, std_qty: int, cfg: Config) -> str:
    """Templated reason with the numbers behind it (shown on draft POs and the dashboard)."""
    flags, qty = rules_out["flags"], rules_out["recommended_qty"]
    uplift = f" incl. {cfg.rules['festival_uplift']}x festival uplift" if m["festival_uplift_applied"] else ""
    cover = (f"{m['weeks_of_cover']:.1f} weeks of cover (stock {m['on_hand']:,.0f} + on order "
             f"{m['on_order']:,.0f}, demand {m['demand_rate']:,.0f}/week{uplift})")
    parts = []
    if STOCKOUT_RISK in flags:
        parts.append(f"STOCKOUT_RISK: {cover} is below the {rules_out['cover_min']:g}-week minimum; "
                     f"{rules_out['candidate_qty'][STOCKOUT_RISK]:,} units brings it to "
                     f"{rules_out['cover_target']:g} weeks")
    if HIGH_DEMAND in flags:
        parts.append(f"HIGH_DEMAND: last 4 weeks averaged {m['avg_4w']:,.0f}/week vs {m['avg_12w']:,.0f} over 12 "
                     f"weeks (> {cfg.rules['high_demand_ratio']}x); order {cfg.rules['high_demand_order_multiple']}x "
                     f"standard ({rules_out['candidate_qty'][HIGH_DEMAND]:,})")
    if OVERSTOCK in flags:
        parts.append(f"OVERSTOCK: {cover} is above the {rules_out['cover_max']:g}-week maximum; pause next order")
    if flags == [OK]:
        parts.append(f"OK: {cover} is within {rules_out['cover_min']:g}-{rules_out['cover_max']:g} weeks; no order")
    if rules_out["conflict"]:
        parts.append("Flags conflict, so no quantity is proposed")
    elif qty and len([f for f in flags if f != OVERSTOCK]) > 1:
        parts.append(f"Larger quantity used: {qty:,}")
    return ". ".join(parts) + "."


def decide_sku(m: dict, perishability: str, std_qty: int, anomalies: list[dict], cfg: Config) -> dict:
    rules_out = apply_rules(m, perishability, std_qty, cfg)
    reasons = route(m, rules_out, std_qty, anomalies, cfg)
    return {
        **rules_out,
        "rule_fired": "+".join(rules_out["flags"]),
        "track": "B" if reasons else "A",
        "exception_reasons": reasons,
        "reason": explain(m, rules_out, std_qty, cfg),
    }
