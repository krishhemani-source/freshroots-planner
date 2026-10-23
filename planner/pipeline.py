"""One planning run, end to end:
kill switch check -> data check gate -> rule engine -> context packs + AI for Track B -> stored results.
"""

import logging
from datetime import date, datetime

import pandas as pd
from sqlalchemy import select

from planner.ai import get_ai_client
from planner.ai.base import AIClient
from planner.ai.step import run_ai_step
from planner.config import ROOT, Config, env, load_config
from planner.context_builder import build_context_packs, load_past_decisions
from planner.data_checks import IST, run_data_checks
from planner.datastore import export_folder
from planner.db import AIResult, DataCheckLog, Run, SkuDecision, get_sessionmaker, to_json
from planner.engine import run_rule_engine
from planner.killswitch import PAUSED, describe, get_state, record_skip, reset_daily_cap_trip

log = logging.getLogger("planner.pipeline")
METRIC_KEYS = ["avg_12w", "avg_4w", "festival_uplift_applied", "demand_rate", "on_hand", "on_order",
               "effective_stock", "weeks_of_cover", "weeks_of_history", "flags", "conflict",
               "candidate_qty", "cover_min", "cover_max", "cover_target", "anomalies", "trend_12w",
               "trend_weeks", "top_distributors"]


def setup_logging() -> None:
    handlers: list[logging.Handler] = [logging.StreamHandler()]
    if not env("VERCEL"):              # Vercel's disk is read-only; its log viewer shows the console
        (ROOT / "logs").mkdir(exist_ok=True)
        handlers.append(logging.FileHandler(ROOT / "logs" / "planner.log"))
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s", handlers=handlers)


def run_planning(run_date: date | None = None, trigger: str = "manual", cfg: Config | None = None,
                 now: datetime | None = None, db_url: str | None = None, ai_client: AIClient | None = None) -> dict:
    cfg = cfg or load_config()
    fixed_now = now                      # only tests pass a fixed time; real runs timestamp each AI request live
    now = now or datetime.now(IST)
    run_date = run_date or now.date()
    Session = get_sessionmaker(db_url)
    log.info("Run for %s started (%s)", run_date, trigger)

    # Kill switch: no runs at all while PAUSED, even when started by hand
    reset_daily_cap_trip(db_url, now)
    state = get_state(db_url)
    if state["mode"] == PAUSED:
        message = f"Run refused: system is {describe(state)}"
        record_skip(run_date, trigger, message, db_url, now)
        log.warning(message)
        return {"run_date": str(run_date), "status": "refused", "message": message}

    # Step 3: data check gate
    gate = run_data_checks(cfg, now=now, folder=export_folder(cfg, db_url))
    with Session() as s:
        s.add(DataCheckLog(run_date=run_date, attempted_at=now.replace(tzinfo=None), trigger=trigger,
                           passed=gate.passed, results=to_json(gate.to_records())))
        s.commit()
        if not gate.passed:
            existing = s.scalar(select(Run).where(Run.run_date == run_date))
            message = "Data check failed: " + "; ".join(f"{r.file}: {r.detail}" for r in gate.failures)
            if existing is None or existing.status != "completed":
                if existing:
                    s.delete(existing)
                    s.flush()
                s.add(Run(run_date=run_date, status="blocked", trigger=trigger, started_at=now.replace(tzinfo=None),
                          finished_at=datetime.now(IST).replace(tzinfo=None), message=message))
            s.commit()
            log.error("Run for %s STOPPED. %s", run_date, message)
            return {"run_date": str(run_date), "status": "blocked", "message": message,
                    "failures": [r.__dict__ for r in gate.failures]}

    # Step 4: rule engine and routing
    decisions = run_rule_engine(gate.frames, run_date, cfg)
    counts = summarise(decisions)

    # Store, replacing any earlier run for the same date (idempotent)
    with Session() as s:
        existing = s.scalar(select(Run).where(Run.run_date == run_date))
        if existing and existing.draft_pos:
            message = (f"Run refused: {len(existing.draft_pos)} draft POs from the {run_date} run were already sent "
                       f"to Zoho, so that run can't be replaced")
            log.warning(message)
            return {"run_date": str(run_date), "status": "refused", "message": message}
        if existing:
            log.info("Replacing earlier run for %s", run_date)
            s.delete(existing)
            s.flush()
        run = Run(run_date=run_date, status="completed", trigger=trigger, started_at=now.replace(tzinfo=None),
                  inputs_summary=inputs_summary(gate.frames), counts=counts, message="")
        run.decisions = [SkuDecision(
            sku_id=d["sku_id"], name=d["name"], category=d["category"], perishability=d["perishability"],
            rule_fired=d["rule_fired"], track=d["track"],
            recommended_qty=None if pd.isna(d["recommended_qty"]) else int(d["recommended_qty"]),
            standard_order_qty=int(d["standard_order_qty"]), unit_cost_inr=float(d["unit_cost_inr"]),
            weeks_of_cover=to_json(float(d["weeks_of_cover"])), exception_reasons=list(d["exception_reasons"]),
            reason=d["reason"], metrics=to_json({k: d[k] for k in METRIC_KEYS}),
        ) for d in decisions.to_dict("records")]
        run.finished_at = datetime.now(IST).replace(tzinfo=None)
        s.add(run)
        s.commit()
        run_id = run.id

    # Steps 5-6: context packs and AI suggestions for Track B (skipped by the AI step if the kill switch says so)
    exceptions = decisions[decisions.track == "B"].to_dict("records")
    packs = build_context_packs(exceptions, decisions, load_past_decisions(db_url))
    ai_results, ai_summary = run_ai_step(packs, cfg, ai_client or get_ai_client(cfg), run_date, db_url, fixed_now)
    with Session() as s:
        run = s.get(Run, run_id)
        run.ai_results = [AIResult(sku_id=p["sku"]["sku_id"], context_pack=p, **ai_results[p["sku"]["sku_id"]])
                          for p in packs]
        counts["ai"] = to_json({**ai_summary, "statuses": pd.Series([r["status"] for r in ai_results.values()],
                                                                    dtype=str).value_counts().to_dict()})
        run.counts = counts
        s.commit()

    log.info("Run for %s completed: %s", run_date, counts)
    return {"run_date": str(run_date), "status": "completed", "run_id": run_id, **counts}


def summarise(d: pd.DataFrame) -> dict:
    order_rows = d[(d.track == "A") & (d.recommended_qty.fillna(0) > 0)]
    return to_json({
        "skus": len(d),
        "track_a": int((d.track == "A").sum()),
        "track_b": int((d.track == "B").sum()),
        "track_b_pct": round(100 * (d.track == "B").mean(), 1),
        "track_a_draft_pos": len(order_rows),
        "track_a_order_value_inr": round(float((order_rows.recommended_qty * order_rows.unit_cost_inr).sum())),
        "flags": d.rule_fired.value_counts().to_dict(),
        "exception_types": pd.Series([r.split(":")[0] for rs in d.exception_reasons for r in rs])
                             .value_counts().to_dict(),
    })


def inputs_summary(frames: dict[str, pd.DataFrame]) -> dict:
    sales = frames["sales_weekly"]
    return {
        "rows": {name: len(df) for name, df in frames.items()},
        "sales_weeks": f"{sales.week_start.min()} to {sales.week_start.max()}",
        "open_po_units": int(frames["zoho_open_pos"]["qty_ordered"].sum()),
    }
