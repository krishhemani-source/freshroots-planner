"""Check the live Gemini setup before a demo. Uses 1 request of the daily quota (0 if cached).

1. Lists the Flash models your API key can use and confirms the configured one is among them.
2. Sends ONE synthetic exception from the latest run through the real AI step and prints the answer.

Usage:  python scripts/check_gemini.py
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select  # noqa: E402

from planner.ai.gemini_client import GeminiClient  # noqa: E402
from planner.ai.step import requests_today, run_ai_step  # noqa: E402
from planner.config import env, load_config, now_ist  # noqa: E402
from planner.db import AIResult, Run, get_sessionmaker  # noqa: E402

if __name__ == "__main__":
    cfg = load_config()
    model = env("GEMINI_MODEL") or cfg.ai["model"]
    if not env("GEMINI_API_KEY"):
        sys.exit("GEMINI_API_KEY is empty in .env")
    client = GeminiClient(model, timeout_seconds=cfg.ai["request_timeout_seconds"])

    flash = sorted(m.name.removeprefix("models/") for m in client.client.models.list() if "flash" in m.name)
    print("Flash models available to this key:\n  " + "\n  ".join(flash))
    if model not in flash:
        sys.exit(f"\nConfigured model {model!r} is not available to this key. Set GEMINI_MODEL in .env to one above.")
    print(f"\nConfigured model {model!r}: available.")

    with get_sessionmaker()() as s:
        run = s.scalars(select(Run).where(Run.status == "completed").order_by(Run.run_date.desc())).first()
        if run is None:
            sys.exit("No completed run yet: run python scripts/run_planning.py first.")
        pack = s.scalars(select(AIResult).where(AIResult.run_id == run.id)).first().context_pack

    results, summary = run_ai_step([pack], cfg, client, run.run_date, apply_breaker=False)
    print(f"\nOne live request for {pack['sku']['sku_id']} ({pack['exception_reasons'][0]}):")
    print(json.dumps(results[pack["sku"]["sku_id"]], indent=2))
    print(f"\nRequests used today: {requests_today(None, now_ist())} of {cfg.ai['daily_request_cap']}")
