"""Importing the professor's case data. Uses a tiny file set in the same format (prof_data/ is not in git)."""

import pandas as pd
import pytest

from planner.case_data import import_case_data, iso_week_monday
from planner.config import load_config
from planner.data_checks import run_data_checks
from planner.pipeline import run_planning
from tests.conftest import AS_OF, NOW

WEEKS = [f"2026-W{w}" for w in range(30, 43)]    # 13 weeks, ending the week of 12 Oct


@pytest.fixture
def case_files(tmp_path):
    src = tmp_path / "prof"
    src.mkdir()
    pd.DataFrame([{"SKU_ID": s, "Week": w, "Units_Sold": u} for s, u in [("SKU001", 100), ("SKU002", 40), ("SKU003", 70)]
                  for w in WEEKS]).to_csv(src / "weekly_sales.csv", index=False)
    pd.DataFrame({"SKU_ID": ["SKU001", "SKU002", "SKU003"], "SKU_Name": ["Mango Drink", "Almond Milk", "Mystery"],
                  "Category": ["Beverage", "Dairy Alt", "Gadgets"], "Perishability": ["Low", "High", "Low"],
                  "Standard_Reorder_Qty": [500, 150, 100], "Unit_of_Measure": "Units",
                  "Distributor_ID": ["DIST001", "DIST002", "DIST001"]}).to_csv(src / "sku_master.csv", index=False)
    pd.DataFrame({"Distributor_ID": ["DIST001", "DIST002"], "Distributor_Name": ["A", "B"],
                  "Region": ["Maharashtra", "Karnataka"], "Status": "Active"}).to_csv(src / "distributor_master.csv", index=False)
    pd.DataFrame({"SKU_ID": ["SKU001", "SKU002", "SKU003"], "Location": "Pune", "Units_On_Hand": [300, 100, 2000],
                  "As_Of_Date": "2026-10-19"}).to_csv(src / "current_inventory.csv", index=False)
    pd.DataFrame({"Year": 2026, "Month": range(1, 13),
                  "Is_Festival_Month": [m in (3, 10, 11) for m in range(1, 13)]}).to_csv(src / "calendar.csv", index=False)
    return src


@pytest.fixture
def case_cfg(case_files, tmp_path):
    cfg = load_config()
    cfg.data.dir = str(tmp_path / "data_case")
    summary = import_case_data(cfg, source=case_files, out_dir=cfg.data.folder, extracted_at=NOW)
    return cfg, summary


def test_iso_weeks_become_mondays():
    assert iso_week_monday("2026-W27") == "2026-06-29"
    assert iso_week_monday("2026-W42") == "2026-10-12"


def test_import_fills_the_gaps(case_cfg):
    cfg, summary = case_cfg
    skus = pd.read_csv(cfg.data.path("sku_master")).set_index("sku_id")
    assert skus.loc["SKU001", "unit_cost_inr"] == 30          # assumed Beverage cost
    assert skus.loc["SKU003", "unit_cost_inr"] == 50          # unknown category -> default
    assert skus.loc["SKU002", "perishability"] == "high"
    assert skus.loc["SKU001", "standard_order_qty"] == 500
    sales = pd.read_csv(cfg.data.path("sales_weekly"))
    assert set(sales["distributor_id"]) == {"DIST001", "DIST002"}   # booked to each SKU's distributor
    assert len(pd.read_csv(cfg.data.path("zoho_open_pos"))) == 0     # no open POs in the case
    assert summary["festival_months_match_config"]
    assert pd.read_csv(cfg.data.manifest_path)["dataset"].iloc[0] == cfg.case_data["label"]


def test_case_data_passes_the_gate_including_the_empty_po_file(case_cfg):
    cfg, _ = case_cfg
    gate = run_data_checks(cfg, now=NOW)
    assert gate.passed, gate.failures


def test_full_run_on_case_data(case_cfg, db_url):
    cfg, _ = case_cfg
    result = run_planning(AS_OF, cfg=cfg, now=NOW, db_url=db_url)
    assert result["status"] == "completed" and result["skus"] == 3
    from planner.service import status
    assert status(cfg, db_url)["modes"]["dataset"] == cfg.case_data["label"]


def test_missing_case_file_is_a_clear_error(case_files, tmp_path):
    (case_files / "calendar.csv").unlink()
    with pytest.raises(FileNotFoundError, match="calendar.csv"):
        import_case_data(load_config(), source=case_files, out_dir=tmp_path / "out")


def test_dataset_switch(monkeypatch):
    monkeypatch.setenv("DATASET", "case")
    assert load_config().data.folder.name == "data_case"
    monkeypatch.setenv("DATASET", "synthetic")
    assert load_config().data.folder.name == "data"
