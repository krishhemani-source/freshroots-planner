"""Generate the synthetic SAP + Zoho export files into data/, and seed Raghav's past decisions.

Usage:  python scripts/generate_data.py [--as-of YYYY-MM-DD]
"""

import argparse
import sys
import time
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from planner.config import load_config  # noqa: E402
from planner.seed_history import seed_past_decisions  # noqa: E402
from planner.synthetic_data import generate  # noqa: E402

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--as-of", type=date.fromisoformat, default=None, help="run date (default: today)")
    args = parser.parse_args()

    started = time.time()
    cfg = load_config()
    summary = generate(cfg, as_of=args.as_of)
    summary["seeded_past_decisions"] = seed_past_decisions(args.as_of or date.today(),
                                                           sku_master=cfg.data.path("sku_master"))
    print("Synthetic data written to data/")
    for key, value in summary.items():
        print(f"  {key:30} {value}")
    print(f"Done in {time.time() - started:.0f}s")
