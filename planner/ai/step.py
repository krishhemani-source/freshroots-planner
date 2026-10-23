"""Step 6: ask the AI about Track B exceptions, within the free-tier limits.

- Skipped entirely unless the kill switch is ACTIVE (AI_OFF -> every SKU marked "AI off").
- Cached answers are reused (keyed by model + context pack), so re-runs cost nothing.
- Up to batch_size SKUs per request, one request at a time, paced to requests_per_minute.
- Before every request the daily cap is checked; reaching it trips the kill switch to AI_OFF.
- 429s are retried after the delay the API asks for, up to max_retries_on_rate_limit; after
  that, this and all remaining SKUs are "AI unavailable" and the run carries on.
- 500/503 "server busy" errors are retried with a growing wait; if they persist, only that batch is
  "AI unavailable" and the next batch is tried.
- Invalid output is retried once; if still invalid, that batch is "AI unavailable".
- If more than batch_failure_trip_pct of a run's batches fail, the kill switch trips to AI_OFF.
- Suggested ranges are clamped to [0, max_qty_multiple x standard_order_qty].
"""

import hashlib
import json
import logging
import time
from datetime import date, datetime

from pydantic import ValidationError
from sqlalchemy import func, select

from planner.ai.base import AIClient, AIRateLimited, AIServerBusy
from planner.ai.schemas import AISuggestion
from planner.config import Config, now_ist
from planner.db import AICache, AIRequest, get_sessionmaker, to_json
from planner.killswitch import (ACTIVE, TRIP_BATCH_FAILURES, TRIP_DAILY_CAP, auto_trip_ai_off, get_state,
                                reset_daily_cap_trip)

log = logging.getLogger("planner.ai")

OK, CACHED, AI_OFF_STATUS, UNAVAILABLE = "ok", "cached", "ai_off", "ai_unavailable"


class RateLimiter:
    """Keeps requests at least 60 / requests_per_minute seconds apart."""

    def __init__(self, per_minute: float | None, sleep=time.sleep, clock=time.monotonic):
        self.interval = 60 / per_minute if per_minute else 0
        self.sleep, self.clock, self.last = sleep, clock, None

    def wait(self) -> None:
        if self.last is not None and self.interval:
            gap = self.interval - (self.clock() - self.last)
            if gap > 0:
                self.sleep(gap)
        self.last = self.clock()


def pack_hash(model: str, pack: dict) -> str:
    return hashlib.sha256(json.dumps({"model": model, "pack": pack}, sort_keys=True, default=str).encode()).hexdigest()


def requests_today(db_url: str | None, now: datetime) -> int:
    midnight = now.replace(tzinfo=None, hour=0, minute=0, second=0, microsecond=0)
    with get_sessionmaker(db_url)() as s:
        return s.scalar(select(func.count()).select_from(AIRequest)
                        .where(AIRequest.counts_toward_quota, AIRequest.requested_at >= midnight))


def parse_batch(raw: str, expected_ids: list[str]) -> dict[str, dict]:
    """Validate the AI's JSON. Raises ValueError if it is not one valid object per SKU in the batch."""
    try:
        items = json.loads(raw)
    except (json.JSONDecodeError, TypeError) as e:
        raise ValueError(f"not JSON: {e}") from e
    if not isinstance(items, list):
        raise ValueError("expected a JSON array")
    try:
        parsed = {s.sku_id: s.model_dump() for s in (AISuggestion.model_validate(i) for i in items)}
    except ValidationError as e:
        raise ValueError(f"schema: {e.errors()[0]['msg']}") from e
    missing = set(expected_ids) - set(parsed)
    if missing:
        raise ValueError(f"missing SKUs: {', '.join(sorted(missing))}")
    return {k: parsed[k] for k in expected_ids}


def clamp(suggestion: dict, std_qty: int, multiple: float) -> tuple[dict, bool]:
    top = int(multiple * std_qty)
    lo = min(max(suggestion["suggested_qty_min"], 0), top)
    hi = min(max(suggestion["suggested_qty_max"], 0), top)
    clamped = (lo, hi) != (suggestion["suggested_qty_min"], suggestion["suggested_qty_max"])
    return {**suggestion, "suggested_qty_min": lo, "suggested_qty_max": hi}, clamped


def run_ai_step(packs: list[dict], cfg: Config, client: AIClient, run_date: date, db_url: str | None = None,
                now: datetime | None = None, sleep=time.sleep, clock=time.monotonic,
                apply_breaker: bool = True) -> tuple[dict, dict]:
    """Returns ({sku_id: {status, suggestion, clamped, note}}, summary).
    apply_breaker=False for one-off checks, which are not planning runs."""
    ai, ks = cfg.ai, cfg.kill_switch
    fixed_now = now                      # tests pass a fixed time; otherwise each request gets the real time
    now = now or now_ist()
    Session = get_sessionmaker(db_url)
    results: dict[str, dict] = {}
    summary = {"exceptions": len(packs), "requests": 0, "batches": 0, "failed_batches": 0, "cached": 0,
               "tripped": None, "model": client.model}

    def outcome(status, note="", suggestion=None, clamped=False):
        return {"status": status, "suggestion": suggestion, "clamped": clamped, "note": note}

    # Kill switch: AI_OFF (or anything but ACTIVE) means no AI calls at all
    reset_daily_cap_trip(db_url, now)
    state = get_state(db_url)
    if state["mode"] != ACTIVE:
        note = f"AI off: kill switch set to {state['mode']} by {state['set_by']} ({state['reason']})"
        return {p["sku"]["sku_id"]: outcome(AI_OFF_STATUS, note) for p in packs}, summary

    # Reuse cached answers
    to_ask = []
    with Session() as s:
        for p in packs:
            sku_id, std = p["sku"]["sku_id"], p["sku"]["standard_order_qty"]
            h = pack_hash(client.model, p)
            hit = s.get(AICache, h)
            if hit:
                suggestion, clamped = clamp(hit.suggestion, std, ai["max_qty_multiple"])
                results[sku_id] = outcome(CACHED, "Reused cached AI answer for identical data", suggestion, clamped)
                summary["cached"] += 1
            else:
                to_ask.append((p, h))

    limiter = RateLimiter(ai["requests_per_minute"] if client.uses_quota else None, sleep, clock)
    batches = [to_ask[i: i + ai["batch_size"]] for i in range(0, len(to_ask), ai["batch_size"])]
    stop: tuple[str, str] | None = None     # (status, note) applied to every batch after a hard stop

    def log_request(batch_no, attempt, ids, status, detail=""):
        with Session() as s:
            s.add(AIRequest(requested_at=(fixed_now or now_ist()).replace(tzinfo=None),
                            run_date=run_date, batch_no=batch_no, attempt=attempt, sku_ids=ids, model=client.model,
                            counts_toward_quota=client.uses_quota, status=status, detail=detail[:500]))
            s.commit()
        summary["requests"] += 1

    for batch_no, batch in enumerate(batches, 1):
        ids = [p["sku"]["sku_id"] for p, _ in batch]
        if stop:
            results.update({i: outcome(*stop) for i in ids})
            continue

        attempt, rate_retries, busy_retries, invalid_retries = 0, 0, 0, 0
        parsed, failure = None, None
        while parsed is None and failure is None:
            if client.uses_quota and requests_today(db_url, now) >= ai["daily_request_cap"]:
                note = f"AI off: daily Gemini cap of {ai['daily_request_cap']} requests reached"
                if ks["trip_on_daily_cap"] and auto_trip_ai_off(TRIP_DAILY_CAP, note, db_url, now):
                    summary["tripped"] = TRIP_DAILY_CAP
                stop = (AI_OFF_STATUS, note)
                break
            limiter.wait()
            attempt += 1
            try:
                raw = client.generate([p for p, _ in batch])
            except AIRateLimited as e:
                log_request(batch_no, attempt, ids, "rate_limited", str(e))
                if rate_retries >= ai["max_retries_on_rate_limit"]:
                    failure = "AI unavailable: rate limit persisted after retries"
                    stop = (UNAVAILABLE, failure)
                else:
                    rate_retries += 1
                    sleep(e.retry_after_s or ai["default_retry_seconds"])
                continue
            except AIServerBusy as e:
                log_request(batch_no, attempt, ids, "server_busy", str(e))
                if busy_retries >= ai["max_retries_on_server_busy"]:
                    failure = "AI unavailable: Gemini overloaded (503) after retries"
                else:
                    sleep(ai["server_busy_retry_seconds"] * 2 ** busy_retries)
                    busy_retries += 1
                continue
            except Exception as e:   # other network or API error: give up on this batch only
                log_request(batch_no, attempt, ids, "error", repr(e))
                failure = f"AI unavailable: {type(e).__name__}"
                continue
            try:
                parsed = parse_batch(raw, ids)
                log_request(batch_no, attempt, ids, "ok")
            except ValueError as e:
                log_request(batch_no, attempt, ids, "invalid", str(e))
                if invalid_retries >= ai["invalid_output_retries"]:
                    failure = "AI unavailable: invalid output after retry"
                invalid_retries += 1

        if attempt:
            summary["batches"] += 1
        if stop and parsed is None and failure is None:      # stopped by the daily cap before asking
            results.update({i: outcome(*stop) for i in ids})
            continue
        if failure:
            summary["failed_batches"] += 1
            results.update({i: outcome(UNAVAILABLE, failure) for i in ids})
            continue

        with Session() as s:
            for p, h in batch:
                sku_id = p["sku"]["sku_id"]
                s.merge(AICache(pack_hash=h, sku_id=sku_id, model=client.model, suggestion=to_json(parsed[sku_id]),
                                created_at=now.replace(tzinfo=None)))
                suggestion, clamped = clamp(parsed[sku_id], p["sku"]["standard_order_qty"], ai["max_qty_multiple"])
                results[sku_id] = outcome(OK, "Range clamped to allowed limits" if clamped else "", suggestion, clamped)
            s.commit()

    # Circuit breaker: too many failed batches in this run
    if apply_breaker and summary["batches"] and 100 * summary["failed_batches"] / summary["batches"] > ks["batch_failure_trip_pct"]:
        note = (f"{summary['failed_batches']} of {summary['batches']} AI batches failed in the {run_date} run "
                f"(limit {ks['batch_failure_trip_pct']}%)")
        if auto_trip_ai_off(TRIP_BATCH_FAILURES, note, db_url, now):
            summary["tripped"] = TRIP_BATCH_FAILURES

    log.info("AI step: %s", summary)
    return results, summary
