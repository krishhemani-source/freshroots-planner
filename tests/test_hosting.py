"""Hosted mode: exports stored in the database, passcode sign-in, cron route, Neon URLs."""

import pytest
from fastapi.testclient import TestClient

from planner.api import AUTH_COOKIE, create_app, passcode_token
from planner.config import now_ist
from planner.data_checks import run_data_checks
from planner.datastore import export_folder, upload_exports
from planner.db import normalise_url
from planner.synthetic_data import generate


def test_neon_urls_are_converted_for_sqlalchemy():
    assert normalise_url("postgres://u:p@host/db?sslmode=require") == "postgresql+psycopg://u:p@host/db?sslmode=require"
    assert normalise_url("postgresql://u:p@host/db").startswith("postgresql+psycopg://")
    assert normalise_url("sqlite:///x.db") == "sqlite:///x.db"


def test_exports_round_trip_through_the_database(clean_data, db_url, monkeypatch):
    result = upload_exports(clean_data, db_url)
    assert len(result["uploaded"]) == 6 and not result["removed"]
    monkeypatch.setenv("DATA_SOURCE", "db")
    folder = export_folder(clean_data, db_url)
    assert folder != clean_data.data.folder
    assert (folder / "sales_weekly.csv").read_bytes() == clean_data.data.path("sales_weekly").read_bytes()


def test_file_deleted_locally_is_deleted_in_the_database_and_gate_fails(clean_data, db_url, monkeypatch):
    from tests.conftest import NOW
    clean_data.data.path("stock_on_hand").unlink()
    assert "stock_on_hand.csv" in upload_exports(clean_data, db_url)["removed"]
    monkeypatch.setenv("DATA_SOURCE", "db")
    gate = run_data_checks(clean_data, now=NOW, folder=export_folder(clean_data, db_url))
    assert not gate.passed and gate.failures[0].file == "stock_on_hand.csv"


@pytest.fixture
def hosted(clean_data, db_url, monkeypatch):
    generate(clean_data, as_of=now_ist().date(), extracted_at=now_ist())
    monkeypatch.setenv("APP_PASSCODE", "festival-2026")
    monkeypatch.setenv("CRON_SECRET", "cron-secret-123")
    return TestClient(create_app(clean_data, db_url))


def test_passcode_required_when_set(hosted):
    assert hosted.get("/api/status").status_code == 401
    hosted.cookies.set(AUTH_COOKIE, passcode_token("wrong"))
    assert hosted.get("/api/status").status_code == 401
    hosted.cookies.set(AUTH_COOKIE, passcode_token("festival-2026"))
    assert hosted.get("/api/status").status_code == 200


def test_cron_needs_the_secret_not_the_passcode(hosted):
    assert hosted.get("/api/cron/scheduled-run").status_code == 401
    r = hosted.get("/api/cron/scheduled-run", headers={"Authorization": "Bearer cron-secret-123"})
    assert r.status_code == 200 and r.json()["status"] == "completed"
    assert r.json().get("track_a", 0) + r.json().get("track_b", 0) == 40


def test_cron_respects_the_kill_switch(hosted, db_url):
    from planner.killswitch import set_mode
    set_mode("PAUSED", "Raghav", "Bad extract", db_url)
    r = hosted.get("/api/cron/scheduled-run", headers={"Authorization": "Bearer cron-secret-123"})
    assert r.json()["status"] == "skipped"


def test_reset_demo_clears_runs_and_kill_switch_but_keeps_ai_cache(clean_data, db_url):
    from sqlalchemy import func, select

    from planner.ai.step import run_ai_step
    from planner.db import AICache, Run, get_sessionmaker
    from planner.demo import reset_demo_state
    from planner.killswitch import get_mode, set_mode
    from planner.pipeline import run_planning
    from tests.conftest import AS_OF, NOW
    from tests.test_killswitch import SpyClient, no_sleep, packs
    run_planning(AS_OF, cfg=clean_data, now=NOW, db_url=db_url)
    run_ai_step(packs(2), clean_data, SpyClient(), AS_OF, db_url, NOW, sleep=no_sleep)
    set_mode("PAUSED", "Raghav", "test", db_url)
    reset_demo_state(clean_data, AS_OF, db_url)
    with get_sessionmaker(db_url)() as s:
        assert s.scalar(select(func.count()).select_from(Run)) == 0
        assert s.scalar(select(func.count()).select_from(AICache)) > 0
    assert get_mode(db_url) == "ACTIVE"
