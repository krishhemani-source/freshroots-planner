"""Deterministic, plausible AI answers for development and tests. Uses no quota."""

import json

from planner.ai.base import AIClient

CAUSES = {
    "NEW_SKU": ("New launch still ramping up; too little history to trust the averages", "low"),
    "CONFLICTING_FLAGS": ("Demand is rising but stock on hand and on order is already high", "medium"),
    "DISTRIBUTOR_ANOMALY": ("A few large distributors changed their buying, e.g. a competitor scheme", "medium"),
    "SUDDEN_SWING": ("One-off event such as a promotion or a lost listing", "medium"),
    "QTY_ABOVE_LIMIT": ("Stock fell far below target, so the rule asks for an unusually large order", "high"),
}


class MockAIClient(AIClient):
    model = "mock"
    uses_quota = False

    def generate(self, packs: list[dict]) -> str:
        return json.dumps([self.suggest(p) for p in packs])

    @staticmethod
    def suggest(pack: dict) -> dict:
        rules = pack["rule_metrics"]
        std = pack["sku"]["standard_order_qty"]
        first_reason = pack["exception_reasons"][0].split(":")[0] if pack["exception_reasons"] else "SUDDEN_SWING"
        cause, confidence = CAUSES.get(first_reason, CAUSES["SUDDEN_SWING"])
        qty = rules.get("recommended_qty")
        if qty is None or first_reason in ("NEW_SKU", "CONFLICTING_FLAGS"):
            qty = std                      # fall back to one standard order
        lo, hi = round(qty * 0.8), round(qty * 1.2)
        return {
            "sku_id": pack["sku"]["sku_id"],
            "likely_cause": cause,
            "suggested_qty_min": lo,
            "suggested_qty_max": hi,
            "confidence": confidence,
            "reasoning": f"[Mock] Routed for {first_reason.replace('_', ' ').lower()}. "
                         f"Rule quantity was {rules.get('recommended_qty')}; a range around it is suggested.",
            "questions_for_raghav": ["Is there a promotion or listing change we don't see in the data?"],
        }
