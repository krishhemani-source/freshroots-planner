"""The data check gate: clean data passes, each kind of bad data stops the run."""

from datetime import timedelta

import pandas as pd

from planner.data_checks import read_export, run_data_checks
from tests.conftest import NOW


def failed_checks(gate):
    return {(r.check, r.file) for r in gate.failures}


def test_clean_data_passes(clean_data):
    gate = run_data_checks(clean_data, now=NOW)
    assert gate.passed, gate.failures
    assert set(gate.frames) == set(clean_data.data.files)


def test_missing_file_fails(clean_data):
    clean_data.data.path("stock_on_hand").unlink()
    gate = run_data_checks(clean_data, now=NOW)
    assert not gate.passed
    assert ("files_present", "stock_on_hand.csv") in failed_checks(gate)


def test_file_older_than_48_hours_fails(clean_data):
    gate = run_data_checks(clean_data, now=NOW + timedelta(hours=49))
    assert not gate.passed
    assert {c for c, _ in failed_checks(gate)} == {"freshness"}


def test_file_exactly_48_hours_old_passes(clean_data):
    assert run_data_checks(clean_data, now=NOW + timedelta(hours=48)).passed


def test_missing_manifest_falls_back_to_file_time(clean_data):
    (clean_data.data.folder / clean_data.data.manifest_file).unlink()
    gate = run_data_checks(clean_data)   # files were just written, so they are fresh
    assert gate.passed


def set_nulls(cfg, file_key, column, share):
    path = cfg.data.path(file_key)
    df = read_export(path)
    n = round(len(df) * share)
    df[column] = df[column].astype("object")
    df.loc[df.index[:n], column] = None
    df.to_csv(path, index=False)


def test_nulls_above_2_percent_fail(clean_data):
    set_nulls(clean_data, "sales_weekly", "units_sold", 0.03)
    gate = run_data_checks(clean_data, now=NOW)
    assert ("nulls", "sales_weekly.csv") in failed_checks(gate)


def test_nulls_at_or_below_2_percent_pass(clean_data):
    set_nulls(clean_data, "sales_weekly", "units_sold", 0.019)
    assert run_data_checks(clean_data, now=NOW).passed


def test_missing_key_column_fails(clean_data):
    path = clean_data.data.path("zoho_open_pos")
    read_export(path).drop(columns=["qty_ordered"]).to_csv(path, index=False)
    gate = run_data_checks(clean_data, now=NOW)
    assert ("nulls", "zoho_open_pos.csv") in failed_checks(gate)


def drop_stock_skus(cfg, n):
    path = cfg.data.path("stock_on_hand")
    read_export(path).iloc[n:].to_csv(path, index=False)


def test_sku_count_off_by_more_than_5_percent_fails(clean_data):
    drop_stock_skus(clean_data, 3)   # 37 of 40 = 7.5% off
    gate = run_data_checks(clean_data, now=NOW)
    assert ("sku_count", "stock_on_hand.csv") in failed_checks(gate)


def test_sku_count_within_5_percent_passes(clean_data):
    drop_stock_skus(clean_data, 2)   # 38 of 40 = 5.0% off
    assert run_data_checks(clean_data, now=NOW).passed


def test_thresholds_come_from_config(clean_data):
    clean_data.data_checks.max_file_age_hours = 1
    gate = run_data_checks(clean_data, now=NOW + timedelta(hours=2))
    assert not gate.passed


def test_every_check_is_reported_even_when_one_fails(clean_data):
    clean_data.data.path("stock_on_hand").unlink()
    gate = run_data_checks(clean_data, now=NOW)
    checks = pd.DataFrame(gate.to_records())
    assert set(checks["check"]) == {"files_present", "freshness", "nulls", "sku_count"}
