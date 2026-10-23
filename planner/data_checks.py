"""Step 3: the data check gate. If any check fails, the run stops.

Four checks, all thresholds from config.yaml:
  1. every export file is present
  2. no file is older than max_file_age_hours (from the export manifest)
  3. no key column has more than max_null_pct nulls
  4. SKU count in stock and recent sales is within max_sku_count_diff_pct of the SKU master
"""

import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

import pandas as pd

from planner.config import IST, Config

log = logging.getLogger("planner.data_checks")


@dataclass
class CheckResult:
    check: str          # files_present / freshness / nulls / sku_count
    file: str
    passed: bool
    detail: str
    value: float | None = None
    threshold: float | None = None


@dataclass
class GateResult:
    passed: bool
    results: list[CheckResult]
    frames: dict[str, pd.DataFrame] = field(default_factory=dict, repr=False)

    @property
    def failures(self) -> list[CheckResult]:
        return [r for r in self.results if not r.passed]

    def to_records(self) -> list[dict]:
        return [asdict(r) for r in self.results]


def read_export(path: Path) -> pd.DataFrame:
    """Read an export CSV; IDs stay as text so 'FR10001' never becomes a number."""
    return pd.read_csv(path, dtype={"sku_id": str, "distributor_id": str, "po_number": str})


def file_timestamps(cfg: Config, folder: Path) -> dict[str, datetime]:
    """extracted_at per file from the export manifest; file modified time if the manifest lacks it."""
    stamps = {}
    manifest = folder / cfg.data.manifest_file
    if manifest.exists():
        m = pd.read_csv(manifest)
        for _, row in m.iterrows():
            ts = pd.to_datetime(row["extracted_at"], errors="coerce")
            if pd.notna(ts):
                stamps[row["file_name"]] = ts.to_pydatetime()
    for file_name in cfg.data.files.values():
        path = folder / file_name
        if file_name not in stamps and path.exists():
            stamps[file_name] = datetime.fromtimestamp(path.stat().st_mtime, tz=IST)
    return stamps


def run_data_checks(cfg: Config, now: datetime | None = None, folder: Path | None = None) -> GateResult:
    checks = cfg.data_checks
    folder = Path(folder or cfg.data.folder)
    now = now or datetime.now(IST)
    results: list[CheckResult] = []
    frames: dict[str, pd.DataFrame] = {}
    stamps = file_timestamps(cfg, folder)

    for name, file_name in cfg.data.files.items():
        path = folder / file_name

        # 1. present
        if not path.exists():
            results.append(CheckResult("files_present", file_name, False, "File is missing"))
            continue
        results.append(CheckResult("files_present", file_name, True, "Found"))

        # 2. fresh
        age_hours = (now - stamps[file_name]).total_seconds() / 3600
        results.append(CheckResult(
            "freshness", file_name, age_hours <= checks.max_file_age_hours,
            f"Extracted {age_hours:.1f} h ago", round(age_hours, 1), checks.max_file_age_hours))

        # 3. nulls in key columns (report the worst column)
        df = read_export(path)
        frames[name] = df
        key_cols = checks.key_columns.get(name, [])
        missing_cols = [c for c in key_cols if c not in df.columns]
        if missing_cols:
            results.append(CheckResult("nulls", file_name, False, f"Key column(s) missing: {', '.join(missing_cols)}"))
            continue
        null_pct = {c: 100 * df[c].isna().mean() if len(df) else 100.0 for c in key_cols}
        worst = max(null_pct, key=null_pct.get) if null_pct else None
        worst_pct = null_pct.get(worst, 0.0)
        results.append(CheckResult(
            "nulls", file_name, worst_pct <= checks.max_null_pct,
            f"Worst key column: {worst} ({worst_pct:.2f}% null)" if worst else "No key columns configured",
            round(worst_pct, 2), checks.max_null_pct))

    # 4. SKU count vs master
    if "sku_master" in frames:
        master_count = frames["sku_master"]["sku_id"].nunique()
        to_compare = {}
        if "stock_on_hand" in frames:
            to_compare[cfg.data.files["stock_on_hand"]] = frames["stock_on_hand"]["sku_id"].nunique()
        if "sales_weekly" in frames:
            sales = frames["sales_weekly"]
            weeks = sorted(sales["week_start"].dropna().unique())[-checks.sales_window_weeks:]
            recent = sales[sales["week_start"].isin(weeks)]
            to_compare[cfg.data.files["sales_weekly"]] = recent["sku_id"].nunique()
        for file_name, count in to_compare.items():
            diff_pct = 100 * abs(count - master_count) / master_count if master_count else 100.0
            results.append(CheckResult(
                "sku_count", file_name, diff_pct <= checks.max_sku_count_diff_pct,
                f"{count} SKUs vs {master_count} in master ({diff_pct:.1f}% difference)",
                round(diff_pct, 1), checks.max_sku_count_diff_pct))

    gate = GateResult(passed=all(r.passed for r in results), results=results, frames=frames)
    for r in gate.failures:
        log.warning("Data check FAILED: %s on %s: %s", r.check, r.file, r.detail)
    log.info("Data check gate %s (%d checks, %d failed)", "PASSED" if gate.passed else "FAILED",
             len(results), len(gate.failures))
    return gate
