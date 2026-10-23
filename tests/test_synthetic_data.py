import pandas as pd

from planner.synthetic_data import generate, last_complete_week_start
from tests.conftest import AS_OF, NOW


def test_last_complete_week_is_the_monday_before_this_week():
    # 22 Oct 2026 is a Thursday -> this week starts 19 Oct -> last full week starts 12 Oct
    assert str(last_complete_week_start(AS_OF)) == "2026-10-12"


def test_same_seed_gives_identical_files(small_cfg, tmp_path):
    generate(small_cfg, out_dir=tmp_path / "a", as_of=AS_OF, extracted_at=NOW)
    generate(small_cfg, out_dir=tmp_path / "b", as_of=AS_OF, extracted_at=NOW)
    for f in ["sku_master.csv", "sales_weekly.csv", "stock_on_hand.csv", "zoho_open_pos.csv"]:
        assert (tmp_path / "a" / f).read_bytes() == (tmp_path / "b" / f).read_bytes()


def test_files_have_the_expected_shape(clean_data):
    cfg = clean_data
    skus = pd.read_csv(cfg.data.path("sku_master"))
    assert len(skus) == 40
    assert set(skus["perishability"]) <= {"low", "medium", "high"}
    assert (skus["perishability"] == "high").mean() == 0.25
    assert pd.read_csv(cfg.data.path("distributors"))["state"].isin(["Maharashtra", "Goa"]).all()
    sales = pd.read_csv(cfg.data.path("sales_weekly"))
    assert (sales["units_sold"] > 0).all()
    assert sales["week_start"].nunique() == 40
    assert pd.read_csv(cfg.data.path("stock_on_hand"))["sku_id"].nunique() == 40


def test_new_launches_have_less_than_12_weeks_of_sales(clean_data):
    cfg = clean_data
    skus = pd.read_csv(cfg.data.path("sku_master"))
    sales = pd.read_csv(cfg.data.path("sales_weekly"))
    weeks_of_history = sales.groupby("sku_id")["week_start"].nunique()
    new = skus[pd.to_datetime(skus["launch_date"]) > pd.Timestamp(AS_OF) - pd.Timedelta(weeks=12)]
    assert len(new) == 2
    assert (weeks_of_history.reindex(new["sku_id"]).fillna(0) < 12).all()
