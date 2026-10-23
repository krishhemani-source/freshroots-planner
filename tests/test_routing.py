"""Exception routing: any one reason sends a SKU to Track B."""

import pandas as pd
import pytest

from planner.config import load_config
from planner.metrics import distributor_changes
from planner.rules import decide_sku
from tests.test_rules import STD, metrics


@pytest.fixture
def cfg():
    return load_config()


def reasons(d):
    return [r.split(":")[0] for r in d["exception_reasons"]]


def test_clean_sku_is_track_a(cfg):
    d = decide_sku(metrics(cfg, on_hand=5000), "low", 2000, [], cfg)   # stockout risk, orders 6,000 = 3x
    assert d["rule_fired"] == "STOCKOUT_RISK"
    assert d["track"] == "A" and d["exception_reasons"] == []


def test_new_sku_under_12_weeks(cfg):
    assert reasons(decide_sku(metrics(cfg, on_hand=8000, history=11), "low", STD, [], cfg)) == ["NEW_SKU"]
    assert decide_sku(metrics(cfg, on_hand=8000, history=12), "low", STD, [], cfg)["track"] == "A"


def test_sudden_swing_over_50_percent(cfg):
    assert "SUDDEN_SWING" in reasons(decide_sku(metrics(cfg, avg_4w=1510, on_hand=8000), "low", STD, [], cfg))
    assert "SUDDEN_SWING" in reasons(decide_sku(metrics(cfg, avg_4w=490, on_hand=8000), "low", STD, [], cfg))
    assert "SUDDEN_SWING" not in reasons(decide_sku(metrics(cfg, avg_4w=1500, on_hand=8000), "low", STD, [], cfg))


def test_qty_above_3x_standard(cfg):
    d = decide_sku(metrics(cfg, on_hand=0), "low", 3000, [], cfg)        # needs 10,000 -> 12,000 > 9,000
    assert d["recommended_qty"] == 12000 and reasons(d) == ["QTY_ABOVE_LIMIT"]
    d = decide_sku(metrics(cfg, on_hand=1000), "low", 3000, [], cfg)     # needs 9,000 -> exactly 3x
    assert d["track"] == "A"


def test_distributor_anomaly_routes_to_track_b(cfg):
    anomaly = [{"distributor_id": "C200001", "change_pct": -45.0}]
    d = decide_sku(metrics(cfg, on_hand=8000), "low", STD, anomaly, cfg)
    assert reasons(d) == ["DISTRIBUTOR_ANOMALY"] and "-45%" in d["exception_reasons"][0]


def test_several_reasons_are_all_recorded(cfg):
    # 10 weeks of history: avg_12w = 12,000 / 10 = 1,200, avg_4w = 2,000 -> +67%
    d = decide_sku(metrics(cfg, avg_4w=2000, on_hand=20000, history=10), "low", STD,
                   [{"distributor_id": "C1", "change_pct": 60.0}], cfg)
    assert set(reasons(d)) == {"NEW_SKU", "CONFLICTING_FLAGS", "DISTRIBUTOR_ANOMALY", "SUDDEN_SWING"}


# --- distributor change detection ------------------------------------------------

def sales_frame(pattern_by_distributor):
    """pattern: {distributor_id: [12 weekly values]} for one SKU."""
    weeks = [f"2026-{m:02d}-01" for m in range(1, 13)]
    return pd.DataFrame([{"week_start": w, "sku_id": "FR1", "distributor_id": d, "units_sold": u}
                         for d, units in pattern_by_distributor.items() for w, u in zip(weeks, units)])


def test_distributor_swing_vs_own_12_week_average(cfg):
    sales = sales_frame({
        "BIG_UP": [100] * 10 + [150, 150],     # 12w avg 108.3, recent 150 -> +38%  -> anomaly
        "BIG_FLAT": [100] * 10 + [120, 120],   # 12w avg 103.3, recent 120 -> +16%  -> fine
        "SMALL_UP": [10] * 10 + [30, 30],      # +200% but averages < 50 units/week -> ignored
    })
    df = distributor_changes(sales, cfg).set_index("distributor_id")
    assert df.loc["BIG_UP", "anomaly"] and df.loc["BIG_UP", "change_pct"] == pytest.approx(38.5, abs=0.1)
    assert not df.loc["BIG_FLAT", "anomaly"]
    assert not df.loc["SMALL_UP", "anomaly"]


def test_only_top_n_distributors_are_checked(cfg):
    cfg.routing["distributor_top_n"] = 1
    sales = sales_frame({"TOP": [500] * 12, "SECOND": [100] * 10 + [10, 10]})
    df = distributor_changes(sales, cfg)
    assert list(df["distributor_id"]) == ["TOP"]
