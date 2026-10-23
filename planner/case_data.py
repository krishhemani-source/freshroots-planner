"""Import the professor's case data into our SAP / Zoho export format.

The case files differ from our exports, so the importer fills the gaps like this:
  weekly_sales.csv       ISO weeks ("2026-W27") become the Monday of that week. The data is per SKU only,
                         so each SKU's sales are booked to its one distributor (Distributor_ID in the
                         SKU master).
  sku_master.csv         Standard_Reorder_Qty is the standard order quantity. There is no unit cost, so an
                         ASSUMED cost per category from config.yaml is used (case_data.assumed_unit_cost_inr).
                         The launch date is the first week with sales.
  distributor_master     There is no city or tier; the region is used as the city and the tier is "unspecified".
  current_inventory.csv  On-hand stock. Its As_Of_Date is kept.
  calendar.csv           Festival months are checked against config.yaml (rules.festival_months).
  (no open POs file)     The case has no open purchase orders, so an empty Zoho file is written.
The import time is recorded as the extraction time, like a fresh SAP export (agreed choice).
"""

from datetime import date, datetime
from pathlib import Path

import pandas as pd

from planner.config import ROOT, Config, now_ist
from planner.synthetic_data import write_manifest

SOURCE_FILES = ["weekly_sales.csv", "sku_master.csv", "distributor_master.csv", "current_inventory.csv", "calendar.csv"]


def iso_week_monday(week: str) -> str:
    """'2026-W27' -> '2026-06-29' (the Monday of ISO week 27)."""
    year, number = week.split("-W")
    return date.fromisocalendar(int(year), int(number), 1).isoformat()


def import_case_data(cfg: Config, source: Path | None = None, out_dir: Path | None = None,
                     extracted_at: datetime | None = None) -> dict:
    case = cfg.case_data
    source = Path(source or ROOT / case["source_dir"])
    out_dir = Path(out_dir or ROOT / case["dir"])
    missing = [f for f in SOURCE_FILES if not (source / f).exists()]
    if missing:
        raise FileNotFoundError(f"Missing in {source}: {', '.join(missing)}")
    out_dir.mkdir(parents=True, exist_ok=True)
    extracted_at = extracted_at or now_ist()

    sales = pd.read_csv(source / "weekly_sales.csv")
    skus = pd.read_csv(source / "sku_master.csv")
    dists = pd.read_csv(source / "distributor_master.csv")
    stock = pd.read_csv(source / "current_inventory.csv")
    calendar = pd.read_csv(source / "calendar.csv")

    sales["week_start"] = sales["Week"].map(iso_week_monday)
    sales = sales.merge(skus[["SKU_ID", "Distributor_ID"]], on="SKU_ID", how="left")
    first_week = sales.groupby("SKU_ID")["week_start"].min()
    costs = skus["Category"].map(case["assumed_unit_cost_inr"]).fillna(case["default_unit_cost_inr"])

    files = {
        "sku_master": pd.DataFrame({
            "sku_id": skus["SKU_ID"], "name": skus["SKU_Name"], "category": skus["Category"],
            "pack_size": skus["Unit_of_Measure"], "perishability": skus["Perishability"].str.lower(),
            "standard_order_qty": skus["Standard_Reorder_Qty"], "unit_cost_inr": costs,
            "launch_date": skus["SKU_ID"].map(first_week)}),
        "distributors": pd.DataFrame({
            "distributor_id": dists["Distributor_ID"], "name": dists["Distributor_Name"], "city": dists["Region"],
            "state": dists["Region"], "region": dists["Region"], "tier": "unspecified"}),
        "sales_weekly": pd.DataFrame({
            "week_start": sales["week_start"], "sku_id": sales["SKU_ID"], "distributor_id": sales["Distributor_ID"],
            "units_sold": sales["Units_Sold"]}).sort_values(["week_start", "sku_id"]),
        "stock_on_hand": pd.DataFrame({
            "sku_id": stock["SKU_ID"], "warehouse": stock["Location"], "on_hand_units": stock["Units_On_Hand"],
            "as_of_date": stock["As_Of_Date"]}),
        "zoho_open_pos": pd.DataFrame(columns=["po_number", "sku_id", "vendor_name", "qty_ordered", "order_date",
                                               "expected_arrival_date", "status"]),
    }
    for name, df in files.items():
        df.to_csv(out_dir / cfg.data.files[name], index=False)
    write_manifest(cfg, out_dir, {n: len(df) for n, df in files.items()}, extracted_at, dataset=case["label"])

    festival = sorted(calendar.loc[calendar["Is_Festival_Month"].astype(str).str.upper() == "TRUE", "Month"].unique())
    return {
        "skus": len(skus), "distributors": len(dists), "sales_rows": len(sales),
        "weeks": f"{sales['week_start'].min()} to {sales['week_start'].max()}",
        "inventory_as_of": ", ".join(sorted(stock["As_Of_Date"].astype(str).unique())),
        "festival_months_in_calendar": [int(m) for m in festival],
        "festival_months_match_config": sorted(cfg.rules["festival_months"]) == [int(m) for m in festival],
        "assumed_unit_cost_inr": dict(zip(skus["Category"], costs)),
        "skus_without_distributor": sales.loc[sales["Distributor_ID"].isna(), "SKU_ID"].unique().tolist(),
    }
