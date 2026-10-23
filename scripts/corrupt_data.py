"""Deliberately break the data to demo the data check gate.

Usage:
  python scripts/corrupt_data.py missing        # delete stock_on_hand.csv
  python scripts/corrupt_data.py stale          # mark all exports as 72 hours old
  python scripts/corrupt_data.py nulls          # blank 5% of on_hand_units
  python scripts/corrupt_data.py sku-mismatch   # drop 10% of SKUs from stock_on_hand.csv
  python scripts/corrupt_data.py restore        # put the clean files back

The clean files are backed up to data/_clean_backup/ before the first corruption.
"""

import shutil
import sys
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from planner.config import load_config  # noqa: E402
from planner.data_checks import IST, read_export  # noqa: E402

MODES = ["missing", "stale", "nulls", "sku-mismatch", "restore"]


def backup_files(folder: Path, names: list[str], backup: Path) -> None:
    if backup.exists():
        return  # keep the first (clean) backup
    backup.mkdir()
    for name in names:
        if (folder / name).exists():
            shutil.copy2(folder / name, backup / name)


def corrupt(mode: str) -> str:
    cfg = load_config()
    folder = cfg.data.folder
    backup = folder / "_clean_backup"
    stock_file = folder / cfg.data.files["stock_on_hand"]
    rng = np.random.default_rng(7)

    if mode == "restore":
        if not backup.exists():
            return "Nothing to restore: no backup found (data is already clean)."
        for f in backup.iterdir():
            shutil.copy2(f, folder / f.name)
        shutil.rmtree(backup)
        return "Clean files restored."

    backup_files(folder, list(cfg.data.files.values()) + [cfg.data.manifest_file], backup)

    if mode == "missing":
        stock_file.unlink(missing_ok=True)
        return f"Deleted {stock_file.name}."
    if mode == "stale":
        manifest = pd.read_csv(folder / cfg.data.manifest_file)
        manifest["extracted_at"] = (datetime.now(IST) - timedelta(hours=72)).isoformat(timespec="seconds")
        manifest.to_csv(folder / cfg.data.manifest_file, index=False)
        return "Marked every export as extracted 72 hours ago."
    if mode == "nulls":
        stock = read_export(stock_file)
        rows = rng.choice(len(stock), int(len(stock) * 0.05), replace=False)
        stock.loc[rows, "on_hand_units"] = None
        stock.to_csv(stock_file, index=False)
        return f"Blanked on_hand_units for {len(rows)} SKUs (5%)."
    if mode == "sku-mismatch":
        stock = read_export(stock_file)
        stock = stock.drop(rng.choice(len(stock), int(len(stock) * 0.10), replace=False))
        stock.to_csv(stock_file, index=False)
        return f"Dropped 10% of SKUs from {stock_file.name} ({len(stock)} left)."
    raise ValueError(mode)


if __name__ == "__main__":
    if len(sys.argv) != 2 or sys.argv[1] not in MODES:
        print(__doc__)
        sys.exit(1)
    print(corrupt(sys.argv[1]))
