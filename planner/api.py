"""HTTP API for the Next.js app. Every route is a thin wrapper over planner/service.py.

Errors: 400 = invalid input (e.g. missing reason), 401 = not signed in, 409 = blocked by the kill switch.

Hosted (Vercel): if APP_PASSCODE is set, every route needs the sign-in cookie set by the web app's
/login page; the cron route instead needs "Authorization: Bearer <CRON_SECRET>", which Vercel Cron sends.
"""

import hashlib
import hmac

from fastapi import Depends, FastAPI, HTTPException, Request
from pydantic import BaseModel

from planner import service
from planner.config import Config, env, load_config
from planner.killswitch import KillSwitchError, SystemPaused, set_mode
from planner.pipeline import run_planning
from planner.po_sender import send_run_drafts
from planner.scheduler import scheduled_run
from planner.zoho import get_zoho_adapter


class ModeChange(BaseModel):
    mode: str
    changed_by: str
    reason: str


class RunRequest(BaseModel):
    started_by: str = ""


class Decision(BaseModel):
    run_id: int
    sku_id: str
    action: str
    qty: int | None = None
    reason: str
    decided_by: str


class SendRequest(BaseModel):
    run_id: int
    sent_by: str


AUTH_COOKIE = "fr_auth"


def passcode_token(passcode: str) -> str:
    """What the sign-in cookie holds: a hash of the passcode, never the passcode itself."""
    return hashlib.sha256(f"freshroots:{passcode}".encode()).hexdigest()


def require_sign_in(request: Request) -> None:
    passcode = env("APP_PASSCODE")
    if passcode and not hmac.compare_digest(request.cookies.get(AUTH_COOKIE, ""), passcode_token(passcode)):
        raise HTTPException(401, "Sign in with the team passcode first")


def create_app(cfg: Config | None = None, db_url: str | None = None) -> FastAPI:
    cfg = cfg or load_config()
    app = FastAPI(title="Freshroots demand planning API")

    @app.get("/api/cron/scheduled-run")
    def cron_run(request: Request):
        """Called by Vercel Cron (Mon & Thu 00:30 UTC = 06:00 IST). Checks the kill switch first."""
        secret = env("CRON_SECRET")
        if not secret or not hmac.compare_digest(request.headers.get("authorization", ""), f"Bearer {secret}"):
            raise HTTPException(401, "Unauthorized")
        return scheduled_run(cfg=cfg, db_url=db_url)

    app.router.dependencies.append(Depends(require_sign_in))   # every route added below needs sign-in

    def guard(fn, *args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except SystemPaused as e:
            raise HTTPException(409, str(e))
        except (KillSwitchError, ValueError) as e:
            raise HTTPException(400, str(e))

    @app.get("/api/status")
    def status():
        return service.status(cfg, db_url)

    @app.get("/api/history")
    def history():
        return service.history(db_url)

    @app.post("/api/killswitch")
    def change_mode(body: ModeChange):
        changed = guard(set_mode, body.mode, body.changed_by, body.reason, db_url)
        return {"changed": changed, **service.status(cfg, db_url)["kill_switch"]}

    @app.post("/api/runs")
    def run_now(body: RunRequest):
        result = run_planning(trigger="manual", cfg=cfg, db_url=db_url)
        if result["status"] == "refused":
            raise HTTPException(409, result["message"])
        return result

    @app.get("/api/exceptions")
    def exceptions(run_id: int | None = None):
        return service.exceptions(db_url, run_id)

    @app.post("/api/exceptions/decision")
    def decide(body: Decision):
        return guard(service.decide, body.run_id, body.sku_id, body.action, body.qty, body.reason,
                     body.decided_by, db_url)

    @app.get("/api/draft-pos")
    def draft_pos(run_id: int | None = None):
        return service.draft_pos(db_url, run_id)

    @app.post("/api/draft-pos/send")
    def send(body: SendRequest):
        return guard(send_run_drafts, body.run_id, body.sent_by, get_zoho_adapter(cfg, db_url), db_url)

    @app.get("/api/dashboard")
    def dashboard(run_id: int | None = None):
        return service.dashboard(db_url, run_id)

    @app.get("/api/scorecard")
    def scorecard():
        return service.scorecard(cfg, db_url)

    return app
