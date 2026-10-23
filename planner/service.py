"""What each app page shows and does. The API (planner/api.py) is a thin layer over these functions."""

from datetime import date, datetime, timedelta

from sqlalchemy import delete, select

from planner.ai.step import requests_today
from planner.config import Config, env, now_ist
from planner.db import (AIRequest, AIResult, DataCheckLog, DraftPO, KillSwitchAudit, ReviewDecision, Run, RunSkip,
                        SkuDecision, get_sessionmaker)
from planner.killswitch import audit_log, get_state
from planner.po_sender import draft_lines, reviews_for_run

ACTIONS = ("approve", "edit", "reject")


def _iso(value):
    return value.isoformat() if isinstance(value, (date, datetime)) else value


def _latest_completed(s, run_id: int | None = None) -> Run | None:
    if run_id is not None:
        return s.get(Run, run_id)
    return s.scalars(select(Run).where(Run.status == "completed").order_by(Run.run_date.desc())).first()


def _run_summary(run: Run | None) -> dict | None:
    if run is None:
        return None
    return {"id": run.id, "run_date": _iso(run.run_date), "status": run.status, "trigger": run.trigger,
            "started_at": _iso(run.started_at), "finished_at": _iso(run.finished_at), "message": run.message,
            "counts": run.counts, "inputs_summary": run.inputs_summary}


# --- shared: banner and run status -------------------------------------------------------

def status(cfg: Config, db_url: str | None = None) -> dict:
    now = now_ist()
    with get_sessionmaker(db_url)() as s:
        latest = s.scalars(select(Run).order_by(Run.run_date.desc())).first()
        completed = _latest_completed(s)
        check = s.scalars(select(DataCheckLog).order_by(DataCheckLog.id.desc())).first()
        pending = 0
        if completed:
            reviewed = reviews_for_run(s, completed)
            pending = sum(1 for d in s.scalars(select(SkuDecision).where(SkuDecision.run_id == completed.id,
                                                                         SkuDecision.track == "B"))
                          if d.sku_id not in reviewed)
    return {
        "kill_switch": {k: _iso(v) for k, v in get_state(db_url).items()},
        "latest_run": _run_summary(latest),
        "latest_completed_run": _run_summary(completed),
        "latest_check": None if check is None else {"run_date": _iso(check.run_date), "attempted_at": _iso(check.attempted_at),
                                                    "trigger": check.trigger, "passed": check.passed, "results": check.results},
        "pending_reviews": pending,
        "ai_today": {"used": requests_today(db_url, now), "cap": cfg.ai["daily_request_cap"]},
        "modes": {"ai": env("AI_MODE", "mock").lower(), "zoho": env("ZOHO_MODE", "mock").lower(),
                  "model": env("GEMINI_MODEL") or cfg.ai["model"],
                  "dataset": (latest.inputs_summary or {}).get("dataset") if latest else None},
    }


def history(db_url: str | None = None, limit: int = 20) -> dict:
    with get_sessionmaker(db_url)() as s:
        runs = s.scalars(select(Run).order_by(Run.run_date.desc()).limit(limit)).all()
        checks = s.scalars(select(DataCheckLog).order_by(DataCheckLog.id.desc()).limit(limit)).all()
        skips = s.scalars(select(RunSkip).order_by(RunSkip.id.desc()).limit(limit)).all()
        return {
            "runs": [_run_summary(r) for r in runs],
            "check_attempts": [{"run_date": _iso(c.run_date), "attempted_at": _iso(c.attempted_at), "trigger": c.trigger,
                                "passed": c.passed, "failures": [r for r in c.results if not r["passed"]]} for c in checks],
            "skips": [{"skipped_at": _iso(k.skipped_at), "run_date": _iso(k.run_date), "trigger": k.trigger,
                       "reason": k.reason} for k in skips],
            "audit": [{k: _iso(v) for k, v in a.items()} for a in audit_log(limit, db_url)],
        }


# --- exceptions: Raghav's review queue -------------------------------------------------------

def _default_qty(ai: AIResult | None, rule_qty: int | None) -> int:
    if ai and ai.suggestion:
        return int(round((ai.suggestion["suggested_qty_min"] + ai.suggestion["suggested_qty_max"]) / 2))
    return int(rule_qty or 0)


def exceptions(db_url: str | None = None, run_id: int | None = None) -> dict:
    with get_sessionmaker(db_url)() as s:
        run = _latest_completed(s, run_id)
        if run is None:
            return {"run": None, "items": []}
        decisions = s.scalars(select(SkuDecision).where(SkuDecision.run_id == run.id, SkuDecision.track == "B")).all()
        ai = {r.sku_id: r for r in s.scalars(select(AIResult).where(AIResult.run_id == run.id))}
        reviews = reviews_for_run(s, run)
        sent = {p.sku_id for p in s.scalars(select(DraftPO).where(DraftPO.run_id == run.id))}
        items = []
        for d in decisions:
            a, r, m = ai.get(d.sku_id), reviews.get(d.sku_id), d.metrics
            items.append({
                "sku_id": d.sku_id, "name": d.name, "category": d.category, "perishability": d.perishability,
                "standard_order_qty": d.standard_order_qty, "unit_cost_inr": d.unit_cost_inr,
                "rule_fired": d.rule_fired, "recommended_qty": d.recommended_qty, "reason": d.reason,
                "exception_reasons": d.exception_reasons, "metrics": m,
                "trend": [{"week": w, "units": u} for w, u in zip(m.get("trend_weeks", []), m.get("trend_12w", []))],
                "top_distributors": m.get("top_distributors", []),
                "ai": None if a is None else {"status": a.status, "suggestion": a.suggestion, "clamped": a.clamped,
                                              "note": a.note},
                "default_qty": _default_qty(a, d.recommended_qty),
                "decision": None if r is None else {"action": r.action, "final_qty": r.final_qty, "reason": r.reason,
                                                    "decided_by": r.decided_by, "decided_at": _iso(r.decided_at)},
                "po_sent": d.sku_id in sent,
            })
    items.sort(key=lambda x: (x["decision"] is not None, x["sku_id"]))   # pending first
    return {"run": _run_summary(run), "items": items}


def decide(run_id: int, sku_id: str, action: str, qty: int | None, reason: str, decided_by: str,
           db_url: str | None = None) -> dict:
    """Save Raghav's decision on one exception. A reason is always required."""
    if action not in ACTIONS:
        raise ValueError(f"Action must be one of {', '.join(ACTIONS)}")
    if not (reason or "").strip():
        raise ValueError("A reason is required for every decision")
    if not (decided_by or "").strip():
        raise ValueError("Say who is deciding")
    with get_sessionmaker(db_url)() as s:
        run = s.get(Run, run_id)
        d = run and s.scalar(select(SkuDecision).where(SkuDecision.run_id == run_id, SkuDecision.sku_id == sku_id))
        if not d or d.track != "B":
            raise ValueError("No such exception in this run")
        if s.scalar(select(DraftPO).where(DraftPO.run_id == run_id, DraftPO.sku_id == sku_id)):
            raise ValueError("The PO for this SKU was already sent to Zoho; it can't be changed here")
        a = s.scalar(select(AIResult).where(AIResult.run_id == run_id, AIResult.sku_id == sku_id))
        final = 0 if action == "reject" else (_default_qty(a, d.recommended_qty) if action == "approve" else qty)
        if final is None or int(final) < 0:
            raise ValueError("Enter a quantity of 0 or more")
        sg = a.suggestion if a else None
        s.execute(delete(ReviewDecision).where(ReviewDecision.run_date == run.run_date,
                                               ReviewDecision.sku_id == sku_id, ReviewDecision.seeded.is_(False)))
        s.add(ReviewDecision(
            run_date=run.run_date, sku_id=sku_id, category=d.category, perishability=d.perishability,
            exception_types=[r.split(":")[0] for r in d.exception_reasons], rule_qty=d.recommended_qty,
            ai_status=a.status if a else "none", ai_qty_min=sg["suggested_qty_min"] if sg else None,
            ai_qty_max=sg["suggested_qty_max"] if sg else None, action=action, final_qty=int(final),
            reason=reason.strip(), decided_by=decided_by.strip(), decided_at=now_ist().replace(tzinfo=None)))
        s.commit()
    return {"sku_id": sku_id, "action": action, "final_qty": int(final)}


# --- draft POs and dashboard ---------------------------------------------------------------

def draft_pos(db_url: str | None = None, run_id: int | None = None) -> dict:
    with get_sessionmaker(db_url)() as s:
        run = _latest_completed(s, run_id)
        if run is None:
            return {"run": None, "lines": [], "pending_reviews": 0}
        lines = draft_lines(s, run)
        reviewed = reviews_for_run(s, run)
        pending = [d.sku_id for d in s.scalars(select(SkuDecision).where(SkuDecision.run_id == run.id,
                                                                         SkuDecision.track == "B"))
                   if d.sku_id not in reviewed]
    return {"run": _run_summary(run), "lines": lines, "pending_reviews": len(pending)}


def dashboard(db_url: str | None = None, run_id: int | None = None) -> dict:
    with get_sessionmaker(db_url)() as s:
        run = _latest_completed(s, run_id)
        if run is None:
            return {"run": None, "rows": []}
        rows = [{"sku_id": d.sku_id, "name": d.name, "category": d.category, "perishability": d.perishability,
                 "rule_fired": d.rule_fired, "flags": d.metrics.get("flags", []), "track": d.track,
                 "recommended_qty": d.recommended_qty, "order_value_inr": round((d.recommended_qty or 0) * d.unit_cost_inr),
                 "weeks_of_cover": d.weeks_of_cover, "avg_12w": d.metrics.get("avg_12w"), "avg_4w": d.metrics.get("avg_4w"),
                 "on_hand": d.metrics.get("on_hand"), "on_order": d.metrics.get("on_order"),
                 "exception_types": [r.split(":")[0] for r in d.exception_reasons], "reason": d.reason}
                for d in s.scalars(select(SkuDecision).where(SkuDecision.run_id == run.id).order_by(SkuDecision.sku_id))]
    return {"run": _run_summary(run), "rows": rows}


# --- scorecard -------------------------------------------------------------------------------

def _monday(d: date) -> date:
    return d - timedelta(days=d.weekday())


def _override_rate(reviews: list[ReviewDecision]) -> tuple[float | None, int]:
    """Share of decisions (with an AI range) where Raghav's final qty fell outside the AI's range."""
    with_ai = [r for r in reviews if r.ai_qty_min is not None and r.ai_qty_max is not None]
    if not with_ai:
        return None, 0
    overrides = sum(not (r.ai_qty_min <= r.final_qty <= r.ai_qty_max) for r in with_ai)
    return round(100 * overrides / len(with_ai), 1), len(with_ai)


def _overstock_value(d: SkuDecision) -> float:
    m = d.metrics
    if "OVERSTOCK" not in d.rule_fired or not m.get("demand_rate"):
        return 0.0
    return max(0.0, m["effective_stock"] - m["cover_max"] * m["demand_rate"]) * d.unit_cost_inr


def scorecard(cfg: Config, db_url: str | None = None, now: datetime | None = None) -> dict:
    now = now or now_ist()
    this_week = _monday(now.date())
    first_week = this_week - timedelta(weeks=cfg.scorecard["weeks_shown"] - 1)
    with get_sessionmaker(db_url)() as s:
        runs = s.scalars(select(Run).where(Run.status == "completed", Run.run_date >= first_week)
                         .order_by(Run.run_date.desc())).all()
        reviews = s.scalars(select(ReviewDecision).where(ReviewDecision.seeded.is_(False),
                                                         ReviewDecision.run_date >= first_week)).all()
        checks = s.scalars(select(DataCheckLog).where(DataCheckLog.run_date >= first_week)).all()
        requests = s.scalars(select(AIRequest).where(AIRequest.counts_toward_quota,
                                                     AIRequest.run_date >= first_week)).all()
        skips = s.scalars(select(RunSkip).where(RunSkip.run_date >= first_week)).all()
        trips = s.scalars(select(KillSwitchAudit).where(KillSwitchAudit.automatic, KillSwitchAudit.new_mode == "AI_OFF",
                                                        KillSwitchAudit.changed_at >= datetime.combine(first_week, datetime.min.time()))).all()

        per_run = []
        for run in runs:
            decisions = s.scalars(select(SkuDecision).where(SkuDecision.run_id == run.id)).all()
            run_reviews = [r for r in reviews if r.run_date == run.run_date]
            rate, n = _override_rate(run_reviews)
            per_run.append({
                "run_id": run.id, "run_date": _iso(run.run_date), "week": _iso(_monday(run.run_date)),
                "stockout_risk_skus": sum("STOCKOUT_RISK" in d.rule_fired for d in decisions),
                "overstock_value_inr": round(sum(_overstock_value(d) for d in decisions)),
                "track_b_share_pct": run.counts.get("track_b_pct"),
                "reviewed": len(run_reviews), "exceptions": run.counts.get("track_b"),
                "override_rate_pct": rate, "decisions_with_ai": n,
                "data_check_failures": sum(not c.passed for c in checks if c.run_date == run.run_date),
                "ai_requests": sum(r.run_date == run.run_date for r in requests),
            })

    weeks = []
    for i in range(cfg.scorecard["weeks_shown"]):
        wk = this_week - timedelta(weeks=i)
        def in_week(d: date, wk=wk) -> bool:
            return _monday(d) == wk
        week_runs = [r for r in per_run if in_week(date.fromisoformat(r["run_date"]))]
        latest = week_runs[0] if week_runs else None
        rate, n = _override_rate([r for r in reviews if in_week(r.run_date)])
        weeks.append({
            "week": _iso(wk), "runs": len(week_runs),
            "stockout_risk_skus": latest and latest["stockout_risk_skus"],
            "overstock_value_inr": latest and latest["overstock_value_inr"],
            "track_b_share_pct": latest and latest["track_b_share_pct"],
            "override_rate_pct": rate, "decisions_with_ai": n,
            "data_check_failures": sum(not c.passed for c in checks if in_week(c.run_date)),
            "ai_requests": sum(in_week(r.run_date) for r in requests),
            "runs_skipped": sum(in_week(k.run_date) for k in skips),
            "auto_trips": sum(in_week(t.changed_at.date()) for t in trips),
        })

    current = weeks[0]
    used, cap = requests_today(db_url, now), cfg.ai["daily_request_cap"]
    values = {
        "override_rate_pct": current["override_rate_pct"],
        "track_b_share_pct": per_run[0]["track_b_share_pct"] if per_run else None,
        "data_check_failures_per_week": current["data_check_failures"],
        "ai_quota_used_pct": round(100 * used / cap, 1) if cap else None,
    }
    criteria = [{"key": k, **c, "value": values.get(k), "breached": values.get(k) is not None and values[k] > c["max"]}
                for k, c in cfg.scorecard["kill_criteria"].items()]
    return {"per_run": per_run, "per_week": weeks, "kill_criteria": criteria,
            "ai_today": {"used": used, "cap": cap},
            "kill_switch_this_week": {"runs_skipped": current["runs_skipped"], "auto_trips": current["auto_trips"]}}
