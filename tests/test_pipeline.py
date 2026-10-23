"""Whole runs: stored results, idempotent re-runs, and the gate stopping a run."""

from datetime import timedelta

from sqlalchemy import func, select

from planner.db import DataCheckLog, Run, SkuDecision, get_sessionmaker
from planner.pipeline import run_planning
from tests.conftest import AS_OF, NOW


def count(db_url, model, *where):
    with get_sessionmaker(db_url)() as s:
        return s.scalar(select(func.count()).select_from(model).where(*where))


def test_run_stores_a_decision_for_every_sku(clean_data, db_url):
    result = run_planning(AS_OF, cfg=clean_data, now=NOW, db_url=db_url)
    assert result["status"] == "completed"
    assert result["track_a"] + result["track_b"] == 40
    assert count(db_url, SkuDecision) == 40
    with get_sessionmaker(db_url)() as s:
        d = s.scalars(select(SkuDecision)).first()
        assert d.rule_fired and d.reason and "avg_12w" in d.metrics


def test_rerunning_the_same_date_replaces_results(clean_data, db_url):
    run_planning(AS_OF, cfg=clean_data, now=NOW, db_url=db_url)
    run_planning(AS_OF, cfg=clean_data, now=NOW, db_url=db_url)
    assert count(db_url, Run) == 1
    assert count(db_url, SkuDecision) == 40
    assert count(db_url, DataCheckLog) == 2        # every attempt is logged


def test_different_dates_are_kept_separately(clean_data, db_url):
    run_planning(AS_OF, cfg=clean_data, now=NOW, db_url=db_url)
    run_planning(AS_OF + timedelta(days=1), cfg=clean_data, now=NOW + timedelta(days=1), db_url=db_url)
    assert count(db_url, Run) == 2


def test_bad_data_stops_the_run_and_nothing_is_forecast(clean_data, db_url):
    clean_data.data.path("stock_on_hand").unlink()
    result = run_planning(AS_OF, cfg=clean_data, now=NOW, db_url=db_url)
    assert result["status"] == "blocked" and "stock_on_hand.csv" in result["message"]
    assert count(db_url, SkuDecision) == 0
    assert count(db_url, Run, Run.status == "blocked") == 1
    assert count(db_url, DataCheckLog, DataCheckLog.passed.is_(False)) == 1


def test_failed_attempt_does_not_wipe_an_earlier_good_run_for_that_date(clean_data, db_url):
    run_planning(AS_OF, cfg=clean_data, now=NOW, db_url=db_url)
    result = run_planning(AS_OF, cfg=clean_data, now=NOW + timedelta(hours=50), db_url=db_url)  # stale
    assert result["status"] == "blocked"
    assert count(db_url, Run, Run.status == "completed") == 1
    assert count(db_url, SkuDecision) == 40
