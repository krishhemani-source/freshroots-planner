"""Synthetic past review decisions by Raghav, so context packs have history from the first demo.

Re-seeding replaces only the seeded rows; real decisions made in the app are never touched.
"""

from datetime import date, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
from sqlalchemy import delete

from planner.db import ReviewDecision, get_sessionmaker

# exception type -> list of (action, how final qty relates to rule qty, reason)
TEMPLATES = {
    "NEW_SKU": [
        ("edit", 1.0, "New launch: first-month sales include trade loading, so kept to one standard order "
                      "until there are 8 weeks of real sell-through."),
        ("approve", None, "Launch is tracking the plan the sales team gave; rule quantity is fine."),
    ],
    "DISTRIBUTOR_ANOMALY": [
        ("edit", 0.8, "Drop is a competitor scheme at a few distributors in one city; expect recovery in 3-4 "
                      "weeks, so ordered 20% below the rule quantity."),
        ("approve", None, "Distributor stocked up ahead of the festival; that is real demand, not a one-off."),
        ("edit", 0.7, "Large distributor has moved part of its range to a rival brand; reducing until the "
                      "sales team confirms."),
    ],
    "SUDDEN_SWING": [
        ("reject", 0.0, "Spike came from a modern-trade promotion that has now ended; no extra order."),
        ("edit", 0.6, "Social media spike; approved a smaller order and will review again on Thursday."),
    ],
    "CONFLICTING_FLAGS": [
        ("reject", 0.0, "Duplicate PO was raised in Zoho; cancelling the duplicate covers the extra demand."),
    ],
    "QTY_ABOVE_LIMIT": [
        ("edit", 0.5, "Plant can't make this in one batch; approved half now and half next run."),
        ("approve", None, "Festival stock is genuinely low and the plant confirmed capacity."),
    ],
}
CATEGORIES = [("Namkeen & Chips", "medium"), ("Bakery & Rusk", "high"), ("Rice & Pulses", "low"),
              ("Sweets & Chikki", "high"), ("Spices & Masala", "low"), ("Biscuits & Cookies", "medium")]


def seed_past_decisions(as_of: date, db_url: str | None = None, n: int = 30, seed: int = 42,
                        sku_master: Path | None = None) -> int:
    rng = np.random.default_rng(seed)
    skus = pd.read_csv(sku_master) if sku_master and Path(sku_master).exists() else None
    rows = []
    for i in range(n):
        exception = list(TEMPLATES)[i % len(TEMPLATES)]
        action, ratio, reason = TEMPLATES[exception][(i // len(TEMPLATES)) % len(TEMPLATES[exception])]
        if skus is not None:
            sku = skus.iloc[int(rng.integers(len(skus)))]
            sku_id, category, perish, std = sku.sku_id, sku.category, sku.perishability, int(sku.standard_order_qty)
        else:
            category, perish = CATEGORIES[i % len(CATEGORIES)]
            sku_id, std = f"FR{10001 + i}", 1000
        rule_qty = None if exception == "CONFLICTING_FLAGS" else int(std * rng.choice([1.5, 2, 3, 4]))
        base = rule_qty or std
        ai_min, ai_max = int(base * 0.7), int(base * 1.1)
        final = base if ratio is None else int(round(base * ratio / 10) * 10)
        decided = datetime.combine(as_of - timedelta(days=int(rng.integers(3, 70))), datetime.min.time()) \
            + timedelta(hours=int(rng.integers(9, 18)))
        rows.append(ReviewDecision(
            run_date=decided.date(), sku_id=sku_id, category=category, perishability=perish,
            exception_types=[exception], rule_qty=rule_qty, ai_status="ok", ai_qty_min=ai_min, ai_qty_max=ai_max,
            action=action, final_qty=final, reason=reason, decided_by="Raghav", decided_at=decided, seeded=True))

    with get_sessionmaker(db_url)() as s:
        s.execute(delete(ReviewDecision).where(ReviewDecision.seeded))
        s.add_all(rows)
        s.commit()
    return len(rows)
