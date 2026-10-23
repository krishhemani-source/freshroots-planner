"""Live Gemini client, tested against a fake SDK client: no API key, no network, no quota."""

import json

import pytest
from google.genai import errors

from planner.ai.base import AIRateLimited
from planner.ai.gemini_client import RESPONSE_SCHEMA, GeminiClient, retry_delay_seconds
from planner.ai.mock_client import MockAIClient
from planner.ai.step import run_ai_step
from planner.config import load_config
from tests.conftest import AS_OF, NOW
from tests.test_killswitch import no_sleep, packs


class FakeResponse:
    def __init__(self, text):
        self.text = text


class FakeGenai:
    """Stands in for google.genai.Client: records calls, answers with `responder(packs)`."""

    def __init__(self, responder):
        self.calls = []
        self.models = self
        self.responder = responder

    def generate_content(self, model, contents, config):
        self.calls.append({"model": model, "contents": contents, "config": config})
        sent = json.loads(contents.split("\n", 1)[1])
        return self.responder(sent)


def rate_limit_error(delay="37s"):
    return errors.ClientError(429, {"error": {"code": 429, "status": "RESOURCE_EXHAUSTED", "message": "quota",
                                              "details": [{"@type": "type.googleapis.com/google.rpc.RetryInfo",
                                                           "retryDelay": delay}]}})


def good(sent):
    return FakeResponse(MockAIClient().generate(sent))


@pytest.fixture
def cfg():
    c = load_config()
    c.ai["batch_size"] = 10
    return c


def test_sends_only_the_context_packs_with_structured_output():
    fake = FakeGenai(good)
    client = GeminiClient("gemini-test", client=fake)
    batch = packs(2)
    client.generate(batch)
    call = fake.calls[0]
    assert call["model"] == "gemini-test"
    assert json.loads(call["contents"].split("\n", 1)[1]) == batch     # nothing but the packs
    assert call["config"].response_mime_type == "application/json"
    assert call["config"].response_json_schema == RESPONSE_SCHEMA


def test_rate_limit_becomes_ai_rate_limited_with_api_delay():
    def limited(_):
        raise rate_limit_error("37s")
    with pytest.raises(AIRateLimited) as e:
        GeminiClient("m", client=FakeGenai(limited)).generate(packs(1))
    assert e.value.retry_after_s == 37


def test_retry_delay_parsing():
    assert retry_delay_seconds(rate_limit_error("12.5s")) == 12.5
    assert retry_delay_seconds(errors.ClientError(429, {"error": {"code": 429, "status": "RESOURCE_EXHAUSTED"}})) is None


def test_other_api_errors_are_not_retried_as_rate_limits():
    def bad_request(_):
        raise errors.ClientError(400, {"error": {"code": 400, "status": "INVALID_ARGUMENT", "message": "bad"}})
    with pytest.raises(errors.ClientError):
        GeminiClient("m", client=FakeGenai(bad_request)).generate(packs(1))


def test_missing_key_is_a_clear_error(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="GEMINI_API_KEY"):
        GeminiClient("m")


# --- through the AI step ------------------------------------------------------------

def test_23_exceptions_take_3_requests(cfg, db_url):
    fake = FakeGenai(good)
    results, summary = run_ai_step(packs(23), cfg, GeminiClient("m", client=fake), AS_OF, db_url, NOW, sleep=no_sleep)
    assert len(fake.calls) == 3 and summary["requests"] == 3
    assert all(r["status"] == "ok" for r in results.values())


def test_rate_limit_then_success_waits_the_api_delay(cfg, db_url):
    answers = iter([rate_limit_error("20s"), None])
    sleeps = []

    def flaky(sent):
        err = next(answers)
        if err:
            raise err
        return good(sent)
    results, summary = run_ai_step(packs(3), cfg, GeminiClient("m", client=FakeGenai(flaky)), AS_OF, db_url, NOW,
                                   sleep=sleeps.append)
    assert 20 in sleeps and summary["requests"] == 2
    assert all(r["status"] == "ok" for r in results.values())


def test_missing_sku_in_answer_retries_once_then_unavailable(cfg, db_url):
    fake = FakeGenai(lambda sent: good(sent[:-1]))          # always drops the last SKU
    results, summary = run_ai_step(packs(3), cfg, GeminiClient("m", client=fake), AS_OF, db_url, NOW, sleep=no_sleep)
    assert len(fake.calls) == 2
    assert all(r["status"] == "ai_unavailable" for r in results.values())


def test_out_of_range_suggestion_is_clamped_and_flagged(cfg, db_url):
    def greedy(sent):
        items = json.loads(MockAIClient().generate(sent))
        for i in items:
            i["suggested_qty_min"], i["suggested_qty_max"] = -50, 99999
        return FakeResponse(json.dumps(items))
    results, _ = run_ai_step(packs(1, std=100), cfg, GeminiClient("m", client=FakeGenai(greedy)), AS_OF, db_url, NOW,
                             sleep=no_sleep)
    r = results["S0"]
    assert r["clamped"] and (r["suggestion"]["suggested_qty_min"], r["suggestion"]["suggested_qty_max"]) == (0, 400)


def test_same_data_twice_uses_cache_not_quota(cfg, db_url):
    fake = FakeGenai(good)
    client = GeminiClient("m", client=fake)
    run_ai_step(packs(5), cfg, client, AS_OF, db_url, NOW, sleep=no_sleep)
    results, summary = run_ai_step(packs(5), cfg, client, AS_OF, db_url, NOW, sleep=no_sleep)
    assert len(fake.calls) == 1 and summary["requests"] == 0 and summary["cached"] == 5


def test_requests_are_paced_to_requests_per_minute(cfg, db_url):
    cfg.ai["batch_size"], cfg.ai["requests_per_minute"] = 1, 5
    sleeps, clock = [], iter(range(0, 1000, 1))
    run_ai_step(packs(3), cfg, GeminiClient("m", client=FakeGenai(good)), AS_OF, db_url, NOW,
                sleep=sleeps.append, clock=lambda: next(clock))
    assert len(sleeps) == 2 and all(s > 10 for s in sleeps)     # ~12 s apart at 5 requests/minute


# --- server overload (503) ------------------------------------------------------------

def overloaded(_):
    raise errors.ServerError(503, {"error": {"code": 503, "status": "UNAVAILABLE", "message": "high demand"}})


def test_503_is_retried_with_growing_waits_then_succeeds(cfg, db_url):
    answers = iter([overloaded, overloaded, good])
    sleeps = []
    results, summary = run_ai_step(packs(2), cfg, GeminiClient("m", client=FakeGenai(lambda s: next(answers)(s))),
                                   AS_OF, db_url, NOW, sleep=sleeps.append)
    assert [s for s in sleeps if s in (15, 30)] == [15, 30] and summary["requests"] == 3   # plus normal pacing
    assert all(r["status"] == "ok" for r in results.values())


def test_persistent_503_fails_only_that_batch(cfg, db_url):
    cfg.ai["batch_size"] = 1
    calls = {"n": 0}

    def first_batch_overloaded(sent):
        calls["n"] += 1
        return overloaded(sent) if sent[0]["sku"]["sku_id"] == "S0" else good(sent)
    results, summary = run_ai_step(packs(3), cfg, GeminiClient("m", client=FakeGenai(first_batch_overloaded)),
                                   AS_OF, db_url, NOW, sleep=no_sleep)
    assert results["S0"]["status"] == "ai_unavailable" and "overloaded" in results["S0"]["note"]
    assert results["S1"]["status"] == results["S2"]["status"] == "ok"
    assert summary["failed_batches"] == 1 and summary["tripped"] is None     # 1 of 3 is not over 50%


def test_one_off_check_never_trips_the_breaker(cfg, db_url):
    from planner.killswitch import ACTIVE, get_mode
    run_ai_step(packs(1), cfg, GeminiClient("m", client=FakeGenai(overloaded)), AS_OF, db_url, NOW,
                sleep=no_sleep, apply_breaker=False)
    assert get_mode(db_url) == ACTIVE
