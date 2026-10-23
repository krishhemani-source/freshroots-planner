"""Reset the system to a clean demo state. Keeps the AI cache and AI request log, so a reset never
spends Gemini quota twice and the daily quota count stays true."""

from datetime import date

from sqlalchemy import delete

from planner.config import Config
from planner.db import (AIResult, DataCheckLog, DraftPO, KillSwitchAudit, MockZohoPO, ReviewDecision, Run, RunSkip,
                        SkuDecision, SystemState, get_sessionmaker)
from planner.seed_history import seed_past_decisions

WIPED = [AIResult, SkuDecision, DraftPO, Run, DataCheckLog, RunSkip, ReviewDecision, MockZohoPO,
         KillSwitchAudit, SystemState]


def reset_demo_state(cfg: Config, as_of: date, db_url: str | None = None) -> dict:
    with get_sessionmaker(db_url)() as s:
        removed = {m.__tablename__: s.execute(delete(m)).rowcount for m in WIPED}
        s.commit()
    seeded = seed_past_decisions(as_of, db_url, sku_master=cfg.data.path("sku_master"))
    return {"removed": removed, "seeded_past_decisions": seeded}
