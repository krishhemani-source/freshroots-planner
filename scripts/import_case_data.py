"""Convert the professor's case files (prof_data/) into our export format (data_case/).

Usage:  python scripts/import_case_data.py
Then set DATASET=case in .env (or prefix commands with DATASET=case) to plan on it.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from planner.case_data import import_case_data  # noqa: E402
from planner.config import load_config  # noqa: E402

if __name__ == "__main__":
    summary = import_case_data(load_config())
    print("Case data written to data_case/")
    for key, value in summary.items():
        print(f"  {key:30} {value}")
    if not summary["festival_months_match_config"]:
        print("  WARNING: the case calendar's festival months differ from rules.festival_months in config.yaml")
