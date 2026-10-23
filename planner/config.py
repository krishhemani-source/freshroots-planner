"""Loads config.yaml (thresholds) and .env (secrets and modes)."""

import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")
IST = timezone(timedelta(hours=5, minutes=30))


def now_ist() -> datetime:
    return datetime.now(IST)


class DataConfig(BaseModel):
    dir: str
    manifest_file: str
    files: dict[str, str]

    @property
    def folder(self) -> Path:
        folder = Path(self.dir)
        return folder if folder.is_absolute() else ROOT / folder

    def path(self, name: str) -> Path:
        """Full path of a data file, e.g. path('sku_master')."""
        return self.folder / self.files.get(name, name)

    @property
    def manifest_path(self) -> Path:
        return self.folder / self.manifest_file


class DataCheckConfig(BaseModel):
    max_file_age_hours: float
    max_null_pct: float
    max_sku_count_diff_pct: float
    sales_window_weeks: int
    key_columns: dict[str, list[str]]


class GeneratorConfig(BaseModel):
    seed: int
    n_skus: int
    n_distributors: int
    weeks_history: int
    n_new_launches: int
    distributors_per_sku: tuple[int, int]
    median_weekly_units_per_sku: float
    share_high_perishability: float
    competitor_scheme_distributors: int
    competitor_scheme_drop: tuple[float, float]
    one_off_events: dict[str, int]
    seasonality: dict[int, float]
    baseline_review_weeks: int
    baseline_data_lag_weeks: int
    baseline_order_every_weeks: dict[str, int]
    baseline_safety_weeks: dict[str, int]
    lead_time_weeks: dict[str, int]
    shelf_life_weeks: dict[str, int]


class Config(BaseModel):
    data: DataConfig
    data_checks: DataCheckConfig
    generator: GeneratorConfig
    rules: dict
    routing: dict
    backtest: dict
    ai: dict
    kill_switch: dict
    schedule: dict
    scorecard: dict
    case_data: dict


def load_config(path: Path | None = None) -> Config:
    """DATASET=case in .env switches the export folder to the professor's case data."""
    path = path or ROOT / "config.yaml"
    with open(path) as f:
        cfg = Config(**yaml.safe_load(f))
    if env("DATASET", "synthetic").lower() == "case":
        cfg.data.dir = cfg.case_data["dir"]
    return cfg


def env(name: str, default: str = "") -> str:
    return os.getenv(name, default)
