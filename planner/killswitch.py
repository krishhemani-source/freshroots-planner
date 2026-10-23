"""The kill switch: one system-wide mode, stored in the database.

  ACTIVE   normal operation
  AI_OFF   runs continue but the AI step is skipped (no Gemini calls, no quota used);
           every Track B exception goes to Raghav marked "AI off"
  PAUSED   no runs at all (scheduled or manual) and nothing can be sent to Zoho;
           existing data stays viewable

Only a person can pause, and only a person can bring the system back from PAUSED.
The system can switch itself to AI_OFF (circuit breakers), and it switches itself back
to ACTIVE the next day only when it tripped because the daily Gemini quota ran out.
Every change is written to the audit log.

Command line:
  python -m planner.killswitch status
  python -m planner.killswitch pause  --by Raghav --reason "SAP extract being re-run"
  python -m planner.killswitch ai-off --by Raghav --reason "Checking AI suggestions by hand this week"
  python -m planner.killswitch resume --by Raghav --reason "Extract fixed"
"""

import argparse
import logging
import sys
from datetime import date, datetime

from sqlalchemy import select

from planner.config import now_ist
from planner.db import KillSwitchAudit, RunSkip, SystemState, get_sessionmaker

log = logging.getLogger("planner.killswitch")

ACTIVE, AI_OFF, PAUSED = "ACTIVE", "AI_OFF", "PAUSED"
MODES = (ACTIVE, AI_OFF, PAUSED)
SYSTEM = "system (automatic)"
TRIP_DAILY_CAP, TRIP_BATCH_FAILURES = "daily_cap", "batch_failures"


class KillSwitchError(Exception):
    """A mode change that is not allowed (missing reason, automatic pause, ...)."""


class SystemPaused(KillSwitchError):
    """Raised when an action is attempted while the system is PAUSED."""


def _naive(now: datetime | None) -> datetime:
    return (now or now_ist()).replace(tzinfo=None)


def _state(session) -> SystemState:
    state = session.get(SystemState, 1)
    if state is None:
        state = SystemState(id=1, mode=ACTIVE, set_by=SYSTEM, set_at=_naive(None), reason="Initial state")
        session.add(state)
        session.flush()
    return state


def get_state(db_url: str | None = None) -> dict:
    with get_sessionmaker(db_url)() as s:
        state = _state(s)
        s.commit()
        return {"mode": state.mode, "set_by": state.set_by, "set_at": state.set_at,
                "reason": state.reason, "auto_trip": state.auto_trip}


def get_mode(db_url: str | None = None) -> str:
    return get_state(db_url)["mode"]


def set_mode(mode: str, changed_by: str, reason: str, db_url: str | None = None, now: datetime | None = None,
             automatic: bool = False, auto_trip: str | None = None) -> bool:
    """Change the mode. Returns False if it was already in that mode (nothing to change)."""
    if mode not in MODES:
        raise KillSwitchError(f"Unknown mode {mode!r}; use one of {', '.join(MODES)}")
    if not (changed_by or "").strip():
        raise KillSwitchError("Say who is making the change")
    if not (reason or "").strip():
        raise KillSwitchError("A reason is required for every mode change")

    with get_sessionmaker(db_url)() as s:
        state = _state(s)
        previous = state.mode
        if automatic:
            if mode == PAUSED:
                raise KillSwitchError("The system never pauses itself; only a person can pause")
            if previous == PAUSED:
                raise KillSwitchError("Only a person can bring the system back from PAUSED")
            if mode == ACTIVE and state.auto_trip != TRIP_DAILY_CAP:
                raise KillSwitchError("The system only re-activates itself after a daily-quota trip")
        if previous == mode:
            return False

        at = _naive(now)
        state.mode, state.set_by, state.set_at, state.reason = mode, changed_by.strip(), at, reason.strip()
        state.auto_trip = auto_trip if automatic and mode == AI_OFF else None
        s.add(KillSwitchAudit(changed_at=at, changed_by=changed_by.strip(), previous_mode=previous, new_mode=mode,
                              reason=reason.strip(), automatic=automatic, auto_trip=state.auto_trip))
        s.commit()
    log.warning("Kill switch: %s -> %s by %s (%s)", previous, mode, changed_by, reason)
    return True


def auto_trip_ai_off(trip: str, reason: str, db_url: str | None = None, now: datetime | None = None) -> bool:
    """Circuit breaker: switch ACTIVE -> AI_OFF. Does nothing if already AI_OFF or PAUSED,
    so a person's AI_OFF is never taken over (and so never auto-reset)."""
    if get_mode(db_url) != ACTIVE:
        return False
    return set_mode(AI_OFF, SYSTEM, reason, db_url, now, automatic=True, auto_trip=trip)


def reset_daily_cap_trip(db_url: str | None = None, now: datetime | None = None) -> bool:
    """On a new day, undo an automatic AI_OFF caused by the daily quota. Nothing else is reset."""
    state = get_state(db_url)
    today = _naive(now).date()
    if state["mode"] == AI_OFF and state["auto_trip"] == TRIP_DAILY_CAP and state["set_at"].date() < today:
        return set_mode(ACTIVE, SYSTEM, "New day: Gemini daily quota has reset", db_url, now, automatic=True)
    return False


def describe(state: dict) -> str:
    return f"{state['mode']} (set by {state['set_by']} at {state['set_at']:%d %b %Y %H:%M}: {state['reason']})"


def require_not_paused(action: str, db_url: str | None = None) -> None:
    state = get_state(db_url)
    if state["mode"] == PAUSED:
        raise SystemPaused(f"Cannot {action}: system is {describe(state)}")


def record_skip(run_date: date, trigger: str, reason: str, db_url: str | None = None,
                now: datetime | None = None) -> None:
    with get_sessionmaker(db_url)() as s:
        s.add(RunSkip(skipped_at=_naive(now), run_date=run_date, trigger=trigger, reason=reason))
        s.commit()


def audit_log(limit: int = 20, db_url: str | None = None) -> list[dict]:
    with get_sessionmaker(db_url)() as s:
        rows = s.scalars(select(KillSwitchAudit).order_by(KillSwitchAudit.id.desc()).limit(limit)).all()
        return [{"changed_at": r.changed_at, "changed_by": r.changed_by, "previous_mode": r.previous_mode,
                 "new_mode": r.new_mode, "reason": r.reason, "automatic": r.automatic} for r in rows]


# --- command line --------------------------------------------------------------

COMMANDS = {"pause": PAUSED, "ai-off": AI_OFF, "resume": ACTIVE}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m planner.killswitch", description="Show or change the system mode.")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("status", help="show the current mode and recent changes")
    for name, mode in COMMANDS.items():
        p = sub.add_parser(name, help=f"switch to {mode}")
        p.add_argument("--by", required=True, help="your name")
        p.add_argument("--reason", required=True, help="why (required)")
    args = parser.parse_args(argv)

    if args.command != "status":
        try:
            changed = set_mode(COMMANDS[args.command], args.by, args.reason)
        except KillSwitchError as e:
            print(f"Not changed: {e}")
            return 1
        if not changed:
            print(f"Already {COMMANDS[args.command]}; nothing changed.")

    print(f"Mode: {describe(get_state())}")
    history = audit_log(5)
    if history:
        print("Recent changes:")
        for h in history:
            auto = " [automatic]" if h["automatic"] else ""
            print(f"  {h['changed_at']:%d %b %H:%M}  {h['previous_mode']:>6} -> {h['new_mode']:<6} "
                  f"by {h['changed_by']}{auto}: {h['reason']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
