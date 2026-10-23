"""Kill switch: modes, audit log, where it is checked, and the automatic circuit breakers."""

import logging
from datetime import timedelta

import pytest
from sqlalchemy import func, select

from planner.ai.base import AIClient, AIRateLimited
from planner.ai.mock_client import MockAIClient
from planner.ai.step import run_ai_step
from planner.config import load_config
from planner.db import AIRequest, AIResult, DataCheckLog, KillSwitchAudit, Run, RunSkip, get_sessionmaker
from planner.killswitch import (ACTIVE, AI_OFF, PAUSED, SYSTEM, TRIP_BATCH_FAILURES, TRIP_DAILY_CAP,
                                KillSwitchError, SystemPaused, audit_log, auto_trip_ai_off, get_mode, get_state,
                                main, reset_daily_cap_trip, set_mode)
from planner.pipeline import run_planning
from planner.po_sender import send_draft_pos
from planner.scheduler import scheduled_run
from planner.zoho.base import ZohoAdapter
from tests.conftest import AS_OF, NOW

TOMORROW = NOW + timedelta(days=1)


# --- fakes --------------------------------------------------------------------------

class SpyClient(AIClient):
    """Answers correctly and counts calls; uses quota like the real Gemini client."""
    model, uses_quota = "spy", True

    def __init__(self):
        self.calls = 0

    def generate(self, packs):
        self.calls += 1
        return MockAIClient().generate(packs)


class InvalidClient(SpyClient):
    def generate(self, packs):
        self.calls += 1
        return "this is not JSON"


class FailFirstBatchClient(SpyClient):
    def generate(self, packs):
        self.calls += 1
        return "oops" if packs[0]["sku"]["sku_id"] == "S0" else MockAIClient().generate(packs)


class RateLimitedClient(SpyClient):
    def generate(self, packs):
        self.calls += 1
        raise AIRateLimited("429 RESOURCE_EXHAUSTED", retry_after_s=7)


class FakeZoho(ZohoAdapter):
    def __init__(self):
        self.sent = []

    def create_draft_po(self, po):
        self.sent.append(po)
        return f"ZOHO-{len(self.sent)}"


def packs(n, std=100):
    return [{"sku": {"sku_id": f"S{i}", "standard_order_qty": std},
             "rule_metrics": {"recommended_qty": std},
             "exception_reasons": ["SUDDEN_SWING: test"]} for i in range(n)]


@pytest.fixture
def cfg():
    c = load_config()
    c.ai["batch_size"] = 1      # one SKU per request makes the counting easy to follow
    return c


def count(db_url, model, *where):
    with get_sessionmaker(db_url)() as s:
        return s.scalar(select(func.count()).select_from(model).where(*where))


def no_sleep(_):
    pass


# --- modes and audit log ----------------------------------------------------------

def test_starts_active(db_url):
    assert get_mode(db_url) == ACTIVE


def test_every_change_writes_an_audit_row(db_url):
    set_mode(PAUSED, "Raghav", "SAP extract being re-run", db_url, NOW)
    set_mode(ACTIVE, "Raghav", "Extract fixed", db_url, NOW + timedelta(hours=1))
    set_mode(AI_OFF, "Priya", "Checking suggestions by hand", db_url, NOW + timedelta(hours=2))
    log = audit_log(10, db_url)[::-1]
    assert [(r["previous_mode"], r["new_mode"]) for r in log] == [(ACTIVE, PAUSED), (PAUSED, ACTIVE), (ACTIVE, AI_OFF)]
    assert log[0]["changed_by"] == "Raghav" and log[0]["reason"] == "SAP extract being re-run"
    assert log[0]["changed_at"] == NOW.replace(tzinfo=None)
    assert log[2]["changed_by"] == "Priya" and not log[2]["automatic"]


def test_no_change_no_audit_row(db_url):
    assert set_mode(ACTIVE, "Raghav", "already active", db_url) is False
    assert count(db_url, KillSwitchAudit) == 0


@pytest.mark.parametrize("mode", [PAUSED, AI_OFF, ACTIVE])
@pytest.mark.parametrize("reason", ["", "   ", None])
def test_reason_is_required(db_url, mode, reason):
    if mode == ACTIVE:
        set_mode(PAUSED, "Raghav", "setup", db_url)
    with pytest.raises(KillSwitchError):
        set_mode(mode, "Raghav", reason, db_url)


def test_resume_from_paused_requires_a_reason(db_url):
    set_mode(PAUSED, "Raghav", "Bad data", db_url)
    with pytest.raises(KillSwitchError):
        set_mode(ACTIVE, "Raghav", "", db_url)
    assert get_mode(db_url) == PAUSED
    assert set_mode(ACTIVE, "Raghav", "Data fixed", db_url)
    assert get_mode(db_url) == ACTIVE


def test_who_is_required(db_url):
    with pytest.raises(KillSwitchError):
        set_mode(PAUSED, " ", "reason", db_url)


# --- PAUSED blocks runs and Zoho ------------------------------------------------------

def test_paused_blocks_manual_run(clean_data, db_url):
    set_mode(PAUSED, "Raghav", "Bad data", db_url, NOW)
    result = run_planning(AS_OF, cfg=clean_data, now=NOW, db_url=db_url, ai_client=SpyClient())
    assert result["status"] == "refused" and "PAUSED" in result["message"]
    assert count(db_url, Run) == 0 and count(db_url, DataCheckLog) == 0
    assert count(db_url, RunSkip, RunSkip.trigger == "manual") == 1


def test_paused_blocks_scheduled_run(clean_data, db_url, caplog):
    set_mode(PAUSED, "Raghav", "Bad data", db_url, NOW)
    with caplog.at_level(logging.WARNING):
        result = scheduled_run(cfg=clean_data, db_url=db_url, now=NOW)
    assert result["status"] == "skipped"
    assert "skipped: system paused" in caplog.text
    assert count(db_url, Run) == 0
    assert count(db_url, RunSkip, RunSkip.trigger == "scheduled", RunSkip.reason == "skipped: system paused") == 1


def test_scheduled_run_goes_ahead_when_active(clean_data, db_url):
    result = scheduled_run(cfg=clean_data, db_url=db_url, now=NOW, ai_client=MockAIClient())
    assert result["status"] == "completed"
    with get_sessionmaker(db_url)() as s:
        assert s.scalars(select(Run)).one().trigger == "scheduled"


def test_paused_blocks_send_to_zoho(db_url):
    zoho = FakeZoho()
    set_mode(PAUSED, "Raghav", "Bad data", db_url)
    with pytest.raises(SystemPaused):
        send_draft_pos([{"sku_id": "S1", "qty": 100}], zoho, "Procurement", db_url)
    assert zoho.sent == []


def test_ai_off_does_not_block_send_to_zoho(db_url):
    zoho = FakeZoho()
    set_mode(AI_OFF, "Raghav", "Manual week", db_url)
    assert send_draft_pos([{"sku_id": "S1", "qty": 100}], zoho, "Procurement", db_url) == ["ZOHO-1"]


# --- AI_OFF -----------------------------------------------------------------------

def test_ai_off_skips_gemini_and_marks_exceptions_ai_off(clean_data, db_url):
    set_mode(AI_OFF, "Raghav", "Manual week", db_url, NOW)
    spy = SpyClient()
    result = run_planning(AS_OF, cfg=clean_data, now=NOW, db_url=db_url, ai_client=spy)
    assert result["status"] == "completed" and result["track_b"] > 0
    assert spy.calls == 0
    assert count(db_url, AIRequest) == 0
    with get_sessionmaker(db_url)() as s:
        rows = s.scalars(select(AIResult)).all()
    assert len(rows) == result["track_b"]
    assert all(r.status == "ai_off" and r.suggestion is None and r.note.startswith("AI off") for r in rows)


# --- circuit breaker: daily quota ---------------------------------------------------

def test_daily_cap_trips_ai_off_and_resets_next_day(cfg, db_url):
    cfg.ai["daily_request_cap"] = 2
    spy = SpyClient()
    results, summary = run_ai_step(packs(5), cfg, spy, AS_OF, db_url, NOW, sleep=no_sleep)
    assert spy.calls == 2
    assert [results[f"S{i}"]["status"] for i in range(5)] == ["ok", "ok", "ai_off", "ai_off", "ai_off"]
    assert summary["tripped"] == TRIP_DAILY_CAP
    state = get_state(db_url)
    assert state["mode"] == AI_OFF and state["auto_trip"] == TRIP_DAILY_CAP and state["set_by"] == SYSTEM
    assert audit_log(1, db_url)[0]["automatic"]

    assert not reset_daily_cap_trip(db_url, NOW + timedelta(hours=3))   # same day: stays off
    assert get_mode(db_url) == AI_OFF
    assert reset_daily_cap_trip(db_url, TOMORROW)                        # next day: back on
    assert get_mode(db_url) == ACTIVE
    assert audit_log(1, db_url)[0]["new_mode"] == ACTIVE


def test_next_run_resets_the_quota_trip_by_itself(cfg, db_url):
    cfg.ai["daily_request_cap"] = 1
    run_ai_step(packs(2), cfg, SpyClient(), AS_OF, db_url, NOW, sleep=no_sleep)
    assert get_mode(db_url) == AI_OFF
    spy = SpyClient()
    results, _ = run_ai_step(packs(1), cfg, spy, AS_OF, db_url, TOMORROW, sleep=no_sleep)
    assert get_mode(db_url) == ACTIVE and spy.calls == 0          # S0 was cached yesterday
    assert results["S0"]["status"] == "cached"


def test_mock_client_never_uses_quota(cfg, db_url):
    cfg.ai["daily_request_cap"] = 1
    results, _ = run_ai_step(packs(4), cfg, MockAIClient(), AS_OF, db_url, NOW, sleep=no_sleep)
    assert all(r["status"] == "ok" for r in results.values())
    assert get_mode(db_url) == ACTIVE


# --- circuit breaker: failed batches -------------------------------------------------

def test_more_than_half_failed_batches_trips_ai_off(cfg, db_url):
    bad = InvalidClient()
    results, summary = run_ai_step(packs(3), cfg, bad, AS_OF, db_url, NOW, sleep=no_sleep)
    assert bad.calls == 6                                   # each batch tried twice (one retry)
    assert all(r["status"] == "ai_unavailable" for r in results.values())
    assert summary["failed_batches"] == 3 and summary["tripped"] == TRIP_BATCH_FAILURES
    assert get_state(db_url)["auto_trip"] == TRIP_BATCH_FAILURES
    assert not reset_daily_cap_trip(db_url, TOMORROW)       # only the quota trip resets itself
    assert get_mode(db_url) == AI_OFF


def test_half_or_fewer_failed_batches_does_not_trip(cfg, db_url):
    results, summary = run_ai_step(packs(3), cfg, FailFirstBatchClient(), AS_OF, db_url, NOW, sleep=no_sleep)
    assert summary["failed_batches"] == 1 and summary["tripped"] is None
    assert results["S0"]["status"] == "ai_unavailable" and results["S1"]["status"] == "ok"
    assert get_mode(db_url) == ACTIVE


def test_rate_limit_retries_then_marks_rest_unavailable(cfg, db_url):
    sleeps = []
    client = RateLimitedClient()
    results, summary = run_ai_step(packs(3), cfg, client, AS_OF, db_url, NOW, sleep=sleeps.append)
    assert client.calls == 4                                # 1 try + 3 retries, then stop
    assert sleeps.count(7) == 3                             # waited the delay the API asked for
    assert all(r["status"] == "ai_unavailable" for r in results.values())
    assert count(db_url, AIRequest, AIRequest.status == "rate_limited") == 4


# --- only people pause, only people un-pause; people's AI_OFF stays ---------------------

def test_human_ai_off_is_not_reset_automatically(cfg, db_url):
    set_mode(AI_OFF, "Raghav", "Reviewing everything by hand this week", db_url, NOW)
    assert not reset_daily_cap_trip(db_url, TOMORROW + timedelta(days=5))
    spy = SpyClient()
    run_ai_step(packs(2), cfg, spy, AS_OF, db_url, TOMORROW, sleep=no_sleep)
    assert spy.calls == 0 and get_mode(db_url) == AI_OFF


def test_auto_trip_does_not_take_over_a_human_ai_off(db_url):
    set_mode(AI_OFF, "Raghav", "Manual week", db_url)
    assert not auto_trip_ai_off(TRIP_DAILY_CAP, "cap", db_url)
    assert get_state(db_url)["set_by"] == "Raghav" and get_state(db_url)["auto_trip"] is None


def test_system_never_pauses_itself(db_url):
    with pytest.raises(KillSwitchError):
        set_mode(PAUSED, SYSTEM, "breaker", db_url, automatic=True)
    assert get_mode(db_url) == ACTIVE


def test_only_a_person_can_leave_paused(db_url):
    set_mode(PAUSED, "Raghav", "Bad data", db_url)
    assert not auto_trip_ai_off(TRIP_BATCH_FAILURES, "breaker", db_url)
    with pytest.raises(KillSwitchError):
        set_mode(ACTIVE, SYSTEM, "auto", db_url, automatic=True)
    assert get_mode(db_url) == PAUSED


# --- command line ---------------------------------------------------------------------

def test_cli_changes_mode_and_requires_reason(db_url, monkeypatch, capsys):
    monkeypatch.setenv("DATABASE_URL", db_url)
    assert main(["pause", "--by", "Raghav", "--reason", "Bad extract"]) == 0
    assert get_mode(db_url) == PAUSED
    with pytest.raises(SystemExit):
        main(["resume", "--by", "Raghav"])                  # --reason missing
    assert main(["resume", "--by", "Raghav", "--reason", " "]) == 1
    assert get_mode(db_url) == PAUSED
    assert main(["status"]) == 0
    assert "PAUSED" in capsys.readouterr().out
