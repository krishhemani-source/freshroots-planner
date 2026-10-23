"""Step 1: scheduled runs, Mon & Thu 06:00 IST (days and time in config.yaml)."""

import logging

from planner.config import Config, load_config, now_ist
from planner.killswitch import PAUSED, get_mode, record_skip
from planner.pipeline import run_planning

log = logging.getLogger("planner.scheduler")
SKIPPED_PAUSED = "skipped: system paused"


def scheduled_run(cfg: Config | None = None, db_url: str | None = None, now=None, **kwargs) -> dict:
    """The job the scheduler fires. Checks the kill switch before doing anything."""
    at = now or now_ist()
    if get_mode(db_url) == PAUSED:
        log.warning("Scheduled run %s", SKIPPED_PAUSED)
        record_skip(at.date(), "scheduled", SKIPPED_PAUSED, db_url, at)
        return {"run_date": str(at.date()), "status": "skipped", "message": SKIPPED_PAUSED}
    return run_planning(trigger="scheduled", cfg=cfg, now=now, db_url=db_url, **kwargs)


def start() -> None:
    """Laptop mode: keep a process running that fires Mon & Thu 06:00 IST. (On Vercel, Vercel Cron does this.)"""
    from apscheduler.schedulers.blocking import BlockingScheduler
    from apscheduler.triggers.cron import CronTrigger

    sch = load_config().schedule
    scheduler = BlockingScheduler(timezone=sch["timezone"])
    scheduler.add_job(scheduled_run, CronTrigger(day_of_week=sch["days"], hour=sch["hour"], minute=sch["minute"],
                                                 timezone=sch["timezone"]), id="planning_run")
    log.info("Scheduler started: %s %02d:%02d %s", sch["days"], sch["hour"], sch["minute"], sch["timezone"])
    scheduler.start()
