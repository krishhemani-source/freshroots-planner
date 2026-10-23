"""Raghav's rules, one test per rule and edge case. Uses the real config.yaml thresholds."""

import math

import numpy as np
import pytest

from planner.config import load_config
from planner.metrics import sku_metrics
from planner.rules import apply_rules, decide_sku

STD = 1000
JUNE, OCTOBER = 6, 10


@pytest.fixture
def cfg():
    return load_config()


def metrics(cfg, avg_12w=1000, avg_4w=None, on_hand=0, on_order=0, month=JUNE, history=52):
    """Build metrics from a simple sales pattern: 8 weeks at one level, last 4 weeks at another,
    chosen so the 12-week and 4-week averages come out exactly as asked."""
    avg_4w = avg_12w if avg_4w is None else avg_4w
    first_8 = (12 * avg_12w - 4 * avg_4w) / 8
    weekly = np.array([first_8] * 8 + [avg_4w] * 4)
    return sku_metrics(weekly, history, on_hand, on_order, month, cfg)


# --- definitions -------------------------------------------------------------

def test_averages_and_cover(cfg):
    m = metrics(cfg, avg_12w=1000, avg_4w=1100, on_hand=7000, on_order=1000)
    assert m["avg_12w"] == 1000 and m["avg_4w"] == 1100
    assert m["effective_stock"] == 8000
    assert m["weeks_of_cover"] == 8.0


def test_new_sku_averaged_over_weeks_it_has_sold(cfg):
    weekly = np.array([0] * 9 + [300, 300, 300])     # launched 3 weeks ago
    m = sku_metrics(weekly, 3, 0, 0, JUNE, cfg)
    assert m["avg_12w"] == 300 and m["avg_4w"] == 300


# --- festival uplift -----------------------------------------------------------

@pytest.mark.parametrize("month", [10, 11, 3])
def test_festival_months_uplift_demand_rate(cfg, month):
    m = metrics(cfg, avg_12w=1000, month=month)
    assert m["demand_rate"] == 1250 and m["festival_uplift_applied"]


def test_no_uplift_outside_festival_months(cfg):
    m = metrics(cfg, avg_12w=1000, month=JUNE)
    assert m["demand_rate"] == 1000 and not m["festival_uplift_applied"]


def test_uplift_is_applied_before_the_cover_check(cfg):
    # 7,000 units: 7 weeks of cover normally (OK), 5.6 weeks in October (stockout risk)
    assert apply_rules(metrics(cfg, on_hand=7000, month=JUNE), "low", STD, cfg)["flags"] == ["OK"]
    assert apply_rules(metrics(cfg, on_hand=7000, month=OCTOBER), "low", STD, cfg)["flags"] == ["STOCKOUT_RISK"]


def test_high_demand_compares_against_raw_avg_12w_even_in_festival_months(cfg):
    out = apply_rules(metrics(cfg, avg_12w=1000, avg_4w=1300, on_hand=10000, month=OCTOBER), "low", STD, cfg)
    assert "HIGH_DEMAND" in out["flags"]


# --- HIGH_DEMAND -----------------------------------------------------------------

def test_high_demand_orders_1_5x_standard(cfg):
    out = apply_rules(metrics(cfg, avg_4w=1250, on_hand=9000), "low", STD, cfg)
    assert out["flags"] == ["HIGH_DEMAND"]
    assert out["recommended_qty"] == 1500


def test_exactly_1_2x_is_not_high_demand(cfg):
    out = apply_rules(metrics(cfg, avg_4w=1200, on_hand=9000), "low", STD, cfg)
    assert out["flags"] == ["OK"]


# --- STOCKOUT_RISK ---------------------------------------------------------------

def test_stockout_qty_brings_cover_to_target_rounded_up(cfg):
    # 4 weeks of cover, target 10 -> need 6,000 -> already a multiple of 1,000
    out = apply_rules(metrics(cfg, on_hand=4000), "low", STD, cfg)
    assert out["flags"] == ["STOCKOUT_RISK"] and out["recommended_qty"] == 6000
    # 4.5 weeks -> need 5,500 -> rounded up to 6,000
    assert apply_rules(metrics(cfg, on_hand=4500), "low", STD, cfg)["recommended_qty"] == 6000


def test_exactly_6_weeks_is_not_stockout_risk(cfg):
    assert apply_rules(metrics(cfg, on_hand=6000), "low", STD, cfg)["flags"] == ["OK"]


def test_stockout_and_high_demand_takes_the_larger_qty(cfg):
    # stockout qty 1,000 (cover 5.5 -> 10 needs 4,500... use small gap) vs high demand 1,500
    small_gap = apply_rules(metrics(cfg, avg_12w=1000, avg_4w=1300, on_hand=5800), "low", 500, cfg)
    assert set(small_gap["flags"]) == {"HIGH_DEMAND", "STOCKOUT_RISK"}
    assert small_gap["candidate_qty"] == {"HIGH_DEMAND": 750, "STOCKOUT_RISK": 4500}
    assert small_gap["recommended_qty"] == 4500
    big_std = apply_rules(metrics(cfg, avg_12w=1000, avg_4w=1300, on_hand=5800), "low", 5000, cfg)
    assert big_std["candidate_qty"] == {"HIGH_DEMAND": 7500, "STOCKOUT_RISK": 5000}
    assert big_std["recommended_qty"] == 7500


# --- OVERSTOCK -----------------------------------------------------------------

def test_overstock_pauses_order(cfg):
    out = apply_rules(metrics(cfg, on_hand=15000), "low", STD, cfg)
    assert out["flags"] == ["OVERSTOCK"] and out["recommended_qty"] == 0


def test_exactly_14_weeks_is_not_overstock(cfg):
    assert apply_rules(metrics(cfg, on_hand=14000), "low", STD, cfg)["flags"] == ["OK"]


def test_zero_demand_with_stock_is_overstock(cfg):
    m = metrics(cfg, avg_12w=0, on_hand=500)
    assert m["weeks_of_cover"] == math.inf
    assert apply_rules(m, "low", STD, cfg)["flags"] == ["OVERSTOCK"]


# --- perishables -------------------------------------------------------------------

@pytest.mark.parametrize("on_hand, expected", [(2900, "STOCKOUT_RISK"), (3000, "OK"), (3500, "OK"),
                                               (4000, "OK"), (4100, "OVERSTOCK")])
def test_perishable_uses_3_and_4_weeks(cfg, on_hand, expected):
    assert apply_rules(metrics(cfg, on_hand=on_hand), "high", STD, cfg)["flags"] == [expected]


def test_same_stock_is_stockout_for_non_perishable(cfg):
    assert apply_rules(metrics(cfg, on_hand=4100), "medium", STD, cfg)["flags"] == ["STOCKOUT_RISK"]


def test_perishable_target_is_3_5_weeks(cfg):
    # 2 weeks of cover -> target 3.5 -> need 1,500 -> rounded to 2,000
    out = apply_rules(metrics(cfg, on_hand=2000), "high", STD, cfg)
    assert out["cover_target"] == 3.5 and out["recommended_qty"] == 2000


# --- on-order handling -----------------------------------------------------------

def test_open_pos_count_towards_stock(cfg):
    out = apply_rules(metrics(cfg, on_hand=3000, on_order=5000), "low", STD, cfg)
    assert out["flags"] == ["OK"]   # 8 weeks of cover once the open PO is counted


def test_on_order_can_be_switched_off(cfg):
    cfg.rules["include_on_order"] = False
    m = metrics(cfg, on_hand=3000, on_order=5000)
    assert m["effective_stock"] == 3000
    assert apply_rules(m, "low", STD, cfg)["flags"] == ["STOCKOUT_RISK"]


# --- conflicting flags -------------------------------------------------------------

def test_high_demand_and_overstock_conflict_has_no_qty_and_goes_to_track_b(cfg):
    m = metrics(cfg, avg_4w=1300, on_hand=20000)
    d = decide_sku(m, "low", STD, [], cfg)
    assert set(d["flags"]) == {"HIGH_DEMAND", "OVERSTOCK"}
    assert d["conflict"] and d["recommended_qty"] is None
    assert d["track"] == "B"
    assert d["exception_reasons"][0].startswith("CONFLICTING_FLAGS")


# --- reasons carry the numbers ------------------------------------------------------

def test_reason_shows_rule_and_numbers(cfg):
    d = decide_sku(metrics(cfg, on_hand=4000, on_order=0), "low", STD, [], cfg)
    assert d["rule_fired"] == "STOCKOUT_RISK"
    assert "4.0 weeks of cover" in d["reason"] and "6,000 units" in d["reason"]


def test_thresholds_come_from_config(cfg):
    cfg.rules["cover_weeks"]["standard"]["min"] = 8
    assert apply_rules(metrics(cfg, on_hand=7000), "low", STD, cfg)["flags"] == ["STOCKOUT_RISK"]
