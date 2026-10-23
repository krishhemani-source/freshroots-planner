import sys
from datetime import date, datetime
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from planner.config import load_config  # noqa: E402
from planner.data_checks import IST  # noqa: E402
from planner.synthetic_data import generate  # noqa: E402

AS_OF = date(2026, 10, 22)
NOW = datetime(2026, 10, 22, 6, 0, tzinfo=IST)


@pytest.fixture
def small_cfg(tmp_path):
    """The real config, pointed at a temp folder and shrunk so tests run in a second."""
    cfg = load_config()
    cfg.data.dir = str(tmp_path / "data")
    g = cfg.generator
    g.n_skus, g.n_distributors, g.weeks_history = 40, 150, 40
    g.n_new_launches, g.distributors_per_sku, g.competitor_scheme_distributors = 2, (5, 10), 1
    return cfg


@pytest.fixture
def clean_data(small_cfg):
    """A freshly generated small dataset, extracted at 06:00 IST on the run date."""
    generate(small_cfg, as_of=AS_OF, extracted_at=NOW)
    return small_cfg


@pytest.fixture
def db_url(tmp_path):
    """A fresh SQLite database per test (the kill switch starts ACTIVE)."""
    return f"sqlite:///{tmp_path / 'test.db'}"


@pytest.fixture(autouse=True)
def never_call_real_services(monkeypatch):
    """Tests always use the mock AI and mock Zoho, whatever .env says."""
    monkeypatch.setenv("AI_MODE", "mock")
    monkeypatch.setenv("ZOHO_MODE", "mock")
    monkeypatch.setenv("DATASET", "synthetic")
