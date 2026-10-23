"""Database tables (SQLite locally, Neon Postgres when DATABASE_URL is set)."""

import math
from datetime import date, datetime
from functools import lru_cache

import numpy as np
from sqlalchemy import (JSON, Boolean, Date, DateTime, Float, ForeignKey, Integer, LargeBinary, String, Text,
                        create_engine)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship, sessionmaker
from sqlalchemy.pool import NullPool

from planner.config import ROOT, env


class Base(DeclarativeBase):
    pass


class Run(Base):
    """One planning run per date. Re-running a date replaces it."""
    __tablename__ = "runs"
    id: Mapped[int] = mapped_column(primary_key=True)
    run_date: Mapped[date] = mapped_column(Date, unique=True, index=True)
    status: Mapped[str] = mapped_column(String(20))          # completed / blocked
    trigger: Mapped[str] = mapped_column(String(20))         # manual / scheduled
    started_at: Mapped[datetime] = mapped_column(DateTime)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    inputs_summary: Mapped[dict] = mapped_column(JSON, default=dict)
    counts: Mapped[dict] = mapped_column(JSON, default=dict)
    message: Mapped[str] = mapped_column(Text, default="")
    decisions: Mapped[list["SkuDecision"]] = relationship(back_populates="run", cascade="all, delete-orphan")
    ai_results: Mapped[list["AIResult"]] = relationship(back_populates="run", cascade="all, delete-orphan")
    draft_pos: Mapped[list["DraftPO"]] = relationship(back_populates="run", cascade="all, delete-orphan")


class DataCheckLog(Base):
    """Every data check attempt, pass or fail (a failed attempt never wipes a good run)."""
    __tablename__ = "data_check_log"
    id: Mapped[int] = mapped_column(primary_key=True)
    run_date: Mapped[date] = mapped_column(Date, index=True)
    attempted_at: Mapped[datetime] = mapped_column(DateTime)
    trigger: Mapped[str] = mapped_column(String(20))
    passed: Mapped[bool] = mapped_column(Boolean)
    results: Mapped[list] = mapped_column(JSON)


class SkuDecision(Base):
    """What the rule engine decided for one SKU in one run, and the numbers behind it."""
    __tablename__ = "sku_decisions"
    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"), index=True)
    sku_id: Mapped[str] = mapped_column(String(20), index=True)
    name: Mapped[str] = mapped_column(String(200))
    category: Mapped[str] = mapped_column(String(60))
    perishability: Mapped[str] = mapped_column(String(10))
    rule_fired: Mapped[str] = mapped_column(String(60))
    track: Mapped[str] = mapped_column(String(1))
    recommended_qty: Mapped[int | None] = mapped_column(Integer, nullable=True)
    standard_order_qty: Mapped[int] = mapped_column(Integer)
    unit_cost_inr: Mapped[float] = mapped_column(Float)
    weeks_of_cover: Mapped[float | None] = mapped_column(Float, nullable=True)
    exception_reasons: Mapped[list] = mapped_column(JSON)
    reason: Mapped[str] = mapped_column(Text)
    metrics: Mapped[dict] = mapped_column(JSON)
    run: Mapped[Run] = relationship(back_populates="decisions")


# --- Raghav's review decisions ---------------------------------------------------

class ReviewDecision(Base):
    """Raghav's approve / edit / reject on one exception. Kept independently of runs, because past
    decisions feed future context packs (re-running a date must not erase what he decided)."""
    __tablename__ = "review_decisions"
    id: Mapped[int] = mapped_column(primary_key=True)
    run_date: Mapped[date] = mapped_column(Date, index=True)
    sku_id: Mapped[str] = mapped_column(String(20), index=True)
    category: Mapped[str] = mapped_column(String(60))
    perishability: Mapped[str] = mapped_column(String(10))
    exception_types: Mapped[list] = mapped_column(JSON)
    rule_qty: Mapped[int | None] = mapped_column(Integer, nullable=True)
    ai_status: Mapped[str] = mapped_column(String(20))
    ai_qty_min: Mapped[int | None] = mapped_column(Integer, nullable=True)
    ai_qty_max: Mapped[int | None] = mapped_column(Integer, nullable=True)
    action: Mapped[str] = mapped_column(String(10))          # approve / edit / reject
    final_qty: Mapped[int] = mapped_column(Integer)
    reason: Mapped[str] = mapped_column(Text)
    decided_by: Mapped[str] = mapped_column(String(100))
    decided_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    seeded: Mapped[bool] = mapped_column(Boolean, default=False)   # synthetic history for the demo


# --- kill switch ---------------------------------------------------------------

class SystemState(Base):
    """The kill switch: a single row (id = 1) holding the current mode."""
    __tablename__ = "system_state"
    id: Mapped[int] = mapped_column(primary_key=True)
    mode: Mapped[str] = mapped_column(String(10))            # ACTIVE / AI_OFF / PAUSED
    set_by: Mapped[str] = mapped_column(String(100))
    set_at: Mapped[datetime] = mapped_column(DateTime)
    reason: Mapped[str] = mapped_column(Text)
    auto_trip: Mapped[str | None] = mapped_column(String(30), nullable=True)   # set when the system tripped itself


class KillSwitchAudit(Base):
    """Every mode change: who, when, why, and what it was before."""
    __tablename__ = "kill_switch_audit"
    id: Mapped[int] = mapped_column(primary_key=True)
    changed_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    changed_by: Mapped[str] = mapped_column(String(100))
    previous_mode: Mapped[str] = mapped_column(String(10))
    new_mode: Mapped[str] = mapped_column(String(10))
    reason: Mapped[str] = mapped_column(Text)
    automatic: Mapped[bool] = mapped_column(Boolean, default=False)
    auto_trip: Mapped[str | None] = mapped_column(String(30), nullable=True)


class RunSkip(Base):
    """A run that was not allowed to start (system paused)."""
    __tablename__ = "run_skips"
    id: Mapped[int] = mapped_column(primary_key=True)
    skipped_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    run_date: Mapped[date] = mapped_column(Date)
    trigger: Mapped[str] = mapped_column(String(20))
    reason: Mapped[str] = mapped_column(Text)


# --- AI step -------------------------------------------------------------------

class AIRequest(Base):
    """One call (attempt) to the AI. Kept even if the run is replaced, so the daily quota count is true."""
    __tablename__ = "ai_requests"
    id: Mapped[int] = mapped_column(primary_key=True)
    requested_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    run_date: Mapped[date] = mapped_column(Date)
    batch_no: Mapped[int] = mapped_column(Integer)
    attempt: Mapped[int] = mapped_column(Integer)
    sku_ids: Mapped[list] = mapped_column(JSON)
    model: Mapped[str] = mapped_column(String(60))
    counts_toward_quota: Mapped[bool] = mapped_column(Boolean)   # False for the mock client
    status: Mapped[str] = mapped_column(String(20))              # ok / invalid / rate_limited / error
    detail: Mapped[str] = mapped_column(Text, default="")


class AICache(Base):
    """AI answers keyed by a hash of the model + context pack, so the same data never spends quota twice."""
    __tablename__ = "ai_cache"
    pack_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    sku_id: Mapped[str] = mapped_column(String(20))
    model: Mapped[str] = mapped_column(String(60))
    suggestion: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime)


class AIResult(Base):
    """What went in to and came out of the AI for one exception SKU in one run."""
    __tablename__ = "ai_results"
    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"), index=True)
    sku_id: Mapped[str] = mapped_column(String(20), index=True)
    status: Mapped[str] = mapped_column(String(20))              # ok / cached / ai_off / ai_unavailable
    context_pack: Mapped[dict] = mapped_column(JSON)
    suggestion: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    clamped: Mapped[bool] = mapped_column(Boolean, default=False)
    note: Mapped[str] = mapped_column(Text, default="")
    run: Mapped[Run] = relationship(back_populates="ai_results")


# --- purchase orders ---------------------------------------------------------------

class DraftPO(Base):
    """A draft PO that was sent to Zoho (Track A rule order or approved Track B exception)."""
    __tablename__ = "draft_pos"
    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"), index=True)
    sku_id: Mapped[str] = mapped_column(String(20))
    name: Mapped[str] = mapped_column(String(200))
    vendor_name: Mapped[str] = mapped_column(String(100))
    qty: Mapped[int] = mapped_column(Integer)
    unit_cost_inr: Mapped[float] = mapped_column(Float)
    source: Mapped[str] = mapped_column(String(10))          # rule (Track A) / review (Track B)
    reason: Mapped[str] = mapped_column(Text)
    zoho_po_id: Mapped[str] = mapped_column(String(60))
    sent_by: Mapped[str] = mapped_column(String(100))
    sent_at: Mapped[datetime] = mapped_column(DateTime)
    run: Mapped[Run] = relationship(back_populates="draft_pos")


class MockZohoPO(Base):
    """Stands in for Zoho Inventory's purchase orders when ZOHO_MODE=mock. Always DRAFT."""
    __tablename__ = "mock_zoho_purchase_orders"
    id: Mapped[int] = mapped_column(primary_key=True)
    po_number: Mapped[str] = mapped_column(String(30), unique=True)
    status: Mapped[str] = mapped_column(String(20), default="draft")
    payload: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime)


# --- export files (hosted mode) --------------------------------------------------------

class ExportFile(Base):
    """The SAP / Zoho export files, gzipped, for the hosted app (Vercel has no writable disk)."""
    __tablename__ = "export_files"
    file_name: Mapped[str] = mapped_column(String(100), primary_key=True)
    content_gzip: Mapped[bytes] = mapped_column(LargeBinary)
    size_bytes: Mapped[int] = mapped_column(Integer)
    uploaded_at: Mapped[datetime] = mapped_column(DateTime)


def normalise_url(url: str) -> str:
    """Neon gives postgres://... ; SQLAlchemy with psycopg 3 needs postgresql+psycopg://..."""
    for prefix in ("postgres://", "postgresql://"):
        if url.startswith(prefix):
            return "postgresql+psycopg://" + url[len(prefix):]
    return url


def database_url() -> str:
    url = env("DATABASE_URL") or env("POSTGRES_URL")      # the Neon integration sets one of these
    if url:
        return normalise_url(url)
    if env("VERCEL"):
        raise RuntimeError("No database configured: connect Neon in Vercel (Storage) so DATABASE_URL is set, "
                           "then redeploy")
    return f"sqlite:///{ROOT / 'db' / 'planner.db'}"


@lru_cache
def get_sessionmaker(url: str | None = None) -> sessionmaker:
    url = normalise_url(url) if url else database_url()
    if url.startswith("sqlite:///"):
        (ROOT / "db").mkdir(exist_ok=True)
        engine = create_engine(url)
    else:   # serverless: open a connection per request; Neon's pooler does the pooling
        engine = create_engine(url, poolclass=NullPool)
    Base.metadata.create_all(engine)
    return sessionmaker(engine, expire_on_commit=False)


def to_json(value):
    """Make numbers JSON-safe: numpy -> Python, infinity/NaN -> None."""
    if isinstance(value, dict):
        return {k: to_json(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [to_json(v) for v in value]
    if isinstance(value, np.generic):
        value = value.item()
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value
