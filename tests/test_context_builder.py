"""Context packs contain exactly the allowed data, and nothing else."""

import json

import pytest

from planner.config import load_config
from planner.context_builder import build_context_packs, load_past_decisions
from planner.data_checks import run_data_checks
from planner.engine import run_rule_engine
from planner.seed_history import seed_past_decisions
from tests.conftest import AS_OF, NOW

ALLOWED_KEYS = {"sku", "sales_trend_12w", "rule_metrics", "exception_reasons", "top_distributors",
                "similar_skus", "raghav_recent_decisions"}


@pytest.fixture
def packs_and_decisions(clean_data, db_url):
    seed_past_decisions(AS_OF, db_url, n=30)
    decisions = run_rule_engine(run_data_checks(clean_data, now=NOW).frames, AS_OF, clean_data)
    exceptions = decisions[decisions.track == "B"].to_dict("records")
    assert exceptions, "small dataset should have at least one exception"
    return build_context_packs(exceptions, decisions, load_past_decisions(db_url)), exceptions, decisions


def test_pack_has_only_the_allowed_sections(packs_and_decisions):
    packs, _, _ = packs_and_decisions
    for p in packs:
        assert set(p) == ALLOWED_KEYS
        assert set(p["sku"]) == {"sku_id", "name", "category", "perishability", "standard_order_qty", "unit_cost_inr"}
        json.dumps(p)   # must be plain JSON (no NaN / numpy types)


def test_sales_trend_is_12_weeks(packs_and_decisions):
    packs, _, _ = packs_and_decisions
    assert all(len(p["sales_trend_12w"]["weekly_units_oldest_first"]) == 12 for p in packs)


def test_top_distributors_are_capped(packs_and_decisions):
    packs, _, _ = packs_and_decisions
    for p in packs:
        normal = [d for d in p["top_distributors"] if not d["anomaly"]]
        assert len(normal) <= 5


def test_similar_skus_same_category_and_perishability(packs_and_decisions):
    packs, _, decisions = packs_and_decisions
    by_id = decisions.set_index("sku_id")
    for p in packs:
        assert len(p["similar_skus"]) <= 3
        for s in p["similar_skus"]:
            assert s["sku_id"] != p["sku"]["sku_id"]
            assert by_id.loc[s["sku_id"], "category"] == p["sku"]["category"]
            assert by_id.loc[s["sku_id"], "perishability"] == p["sku"]["perishability"]


def test_past_decisions_match_the_exception_type(packs_and_decisions):
    packs, _, _ = packs_and_decisions
    found_any = False
    for p in packs:
        types = {r.split(":")[0] for r in p["exception_reasons"]}
        assert len(p["raghav_recent_decisions"]) <= 5
        for d in p["raghav_recent_decisions"]:
            found_any = True
            assert types & set(d["exception_types"]) and d["reason"]
    assert found_any


def test_conflicting_flags_pack_has_no_rule_qty(packs_and_decisions):
    packs, exceptions, _ = packs_and_decisions
    for p, d in zip(packs, exceptions):
        if d["conflict"]:
            assert p["rule_metrics"]["recommended_qty"] is None


def test_reseeding_keeps_real_decisions(db_url):
    from planner.db import ReviewDecision, get_sessionmaker
    with get_sessionmaker(db_url)() as s:
        s.add(ReviewDecision(run_date=AS_OF, sku_id="FR1", category="x", perishability="low", exception_types=["NEW_SKU"],
                             rule_qty=1, ai_status="ok", action="approve", final_qty=1, reason="real",
                             decided_by="Raghav", decided_at=NOW.replace(tzinfo=None)))
        s.commit()
    seed_past_decisions(AS_OF, db_url, n=10)
    seed_past_decisions(AS_OF, db_url, n=10)
    past = load_past_decisions(db_url)
    assert len(past) == 11 and sum(p["reason"] == "real" for p in past) == 1


def test_pipeline_stores_full_packs(clean_data, db_url):
    from sqlalchemy import select

    from planner.ai.mock_client import MockAIClient
    from planner.db import AIResult, get_sessionmaker
    from planner.pipeline import run_planning
    run_planning(AS_OF, cfg=clean_data, now=NOW, db_url=db_url, ai_client=MockAIClient())
    with get_sessionmaker(db_url)() as s:
        stored = s.scalars(select(AIResult)).all()
    assert stored and all(set(r.context_pack) == ALLOWED_KEYS for r in stored)
