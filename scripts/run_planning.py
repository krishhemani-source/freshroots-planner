"""Run one planning cycle from the command line (same code as the "Run now" button).

Usage:  python scripts/run_planning.py [--date YYYY-MM-DD]
"""

import argparse
import json
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from planner.pipeline import run_planning, setup_logging  # noqa: E402

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", type=date.fromisoformat, default=None, help="run date (default: today)")
    args = parser.parse_args()
    setup_logging()
    result = run_planning(run_date=args.date)
    print(json.dumps(result, indent=2, default=str))
    sys.exit(0 if result["status"] == "completed" else 1)
