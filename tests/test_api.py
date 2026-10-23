"""The API behind the app: every page's data, Raghav's decisions, sending POs, and kill switch blocks."""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from planner.api import create_app
from planner.config import now_ist
from planner.db import MockZohoPO, get_sessionmaker
from planner.synthetic_data import generate


@pytest.fixture
def api(clean_data, db_url):
    # "Run now" uses the real clock, so stamp the data with today's date and time
    generate(clean_data, as_of=now_ist().date(), extracted_at=now_ist())
    client = TestClient(create_app(clean_data, db_url))
    assert client.post("/api/runs", json={"started_by": "Raghav"}).status_code == 200
    return client


def first_exception(api):
    data = api.get("/api/exceptions").json()
    assert data["items"], "small dataset should have exceptions"
    return data["run"]["id"], data["items"][0]


def decide(api, run_id, sku_id, action, qty=None, reason="Checked with sales team"):
    return api.post("/api/exceptions/decision", json={"run_id": run_id, "sku_id": sku_id, "action": action,
                                                       "qty": qty, "reason": reason, "decided_by": "Raghav"})


def test_every_page_loads(api):
    for path in ["/api/status", "/api/history", "/api/exceptions", "/api/draft-pos", "/api/dashboard", "/api/scorecard"]:
        assert api.get(path).status_code == 200, path
    assert len(api.get("/api/dashboard").json()["rows"]) == 40
    assert api.get("/api/status").json()["latest_run"]["status"] == "completed"


def test_decision_needs_a_reason(api):
    run_id, item = first_exception(api)
    assert decide(api, run_id, item["sku_id"], "approve", reason="  ").status_code == 400


def test_approve_edit_reject(api):
    run_id, item = first_exception(api)
    sku = item["sku_id"]
    assert decide(api, run_id, sku, "approve").json()["final_qty"] == item["default_qty"]
    assert decide(api, run_id, sku, "edit", qty=1234).json()["final_qty"] == 1234      # replaces the approval
    assert decide(api, run_id, sku, "reject").json()["final_qty"] == 0
    saved = next(i for i in api.get("/api/exceptions").json()["items"] if i["sku_id"] == sku)
    assert saved["decision"]["action"] == "reject" and saved["decision"]["reason"] == "Checked with sales team"


def test_approved_exception_joins_draft_pos_rejected_does_not(api):
    run_id, item = first_exception(api)
    before = {l["sku_id"] for l in api.get("/api/draft-pos").json()["lines"]}
    assert item["sku_id"] not in before
    decide(api, run_id, item["sku_id"], "edit", qty=500)
    line = next(l for l in api.get("/api/draft-pos").json()["lines"] if l["sku_id"] == item["sku_id"])
    assert line["source"] == "review" and line["qty"] == 500 and "Checked with sales team" in line["reason"]
    decide(api, run_id, item["sku_id"], "reject")
    assert item["sku_id"] not in {l["sku_id"] for l in api.get("/api/draft-pos").json()["lines"]}


def test_send_to_zoho_writes_mock_drafts_once(api, db_url):
    data = api.get("/api/draft-pos").json()
    n = len(data["lines"])
    assert n > 0 and all(l["source"] == "rule" for l in data["lines"])
    r = api.post("/api/draft-pos/send", json={"run_id": data["run"]["id"], "sent_by": "Procurement"})
    assert r.status_code == 200 and r.json()["sent"] == n
    with get_sessionmaker(db_url)() as s:
        assert s.scalar(select(func.count()).select_from(MockZohoPO).where(MockZohoPO.status == "draft")) == n
    lines = api.get("/api/draft-pos").json()["lines"]
    assert all(l["status"] == "sent" and l["zoho_po_id"].startswith("MOCK-PO-") for l in lines)
    assert api.post("/api/draft-pos/send", json={"run_id": data["run"]["id"], "sent_by": "Procurement"}).json()["sent"] == 0


def test_after_sending_the_run_cannot_be_replaced(api):
    run_id = api.get("/api/draft-pos").json()["run"]["id"]
    api.post("/api/draft-pos/send", json={"run_id": run_id, "sent_by": "Procurement"})
    r = api.post("/api/runs", json={})
    assert r.status_code == 409 and "already sent" in r.json()["detail"]


def test_paused_blocks_run_and_send_but_pages_still_load(api):
    run_id = api.get("/api/draft-pos").json()["run"]["id"]
    r = api.post("/api/killswitch", json={"mode": "PAUSED", "changed_by": "Raghav", "reason": "Bad extract"})
    assert r.status_code == 200 and r.json()["mode"] == "PAUSED"
    assert api.post("/api/runs", json={}).status_code == 409
    assert api.post("/api/draft-pos/send", json={"run_id": run_id, "sent_by": "Procurement"}).status_code == 409
    assert api.get("/api/dashboard").status_code == 200
    assert api.get("/api/status").json()["kill_switch"]["set_by"] == "Raghav"


def test_mode_change_needs_reason(api):
    assert api.post("/api/killswitch", json={"mode": "AI_OFF", "changed_by": "Raghav", "reason": ""}).status_code == 400


def test_scorecard_override_rate_and_kill_criteria(api, clean_data):
    run_id, item = first_exception(api)
    sg = item["ai"]["suggestion"]
    decide(api, run_id, item["sku_id"], "edit", qty=sg["suggested_qty_max"] + 1000)   # outside the AI range
    card = api.get("/api/scorecard").json()
    assert card["per_run"][0]["override_rate_pct"] == 100.0
    override = next(c for c in card["kill_criteria"] if c["key"] == "override_rate_pct")
    assert override["breached"] and override["max"] == clean_data.scorecard["kill_criteria"]["override_rate_pct"]["max"]


def test_scorecard_counts_skips_and_auto_trips(api):
    api.post("/api/killswitch", json={"mode": "PAUSED", "changed_by": "Raghav", "reason": "Bad extract"})
    api.post("/api/runs", json={})
    week = api.get("/api/scorecard").json()["kill_switch_this_week"]
    assert week["runs_skipped"] == 1
