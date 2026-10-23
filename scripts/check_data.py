"""Run only the data check gate and print the results.

Usage:  python scripts/check_data.py
Exit code 0 = passed, 1 = failed.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from planner.config import load_config  # noqa: E402
from planner.data_checks import run_data_checks  # noqa: E402

if __name__ == "__main__":
    gate = run_data_checks(load_config())
    for r in gate.results:
        print(f"  {'PASS' if r.passed else 'FAIL'}  {r.check:14} {r.file:22} {r.detail}")
    print()
    if gate.passed:
        print("DATA CHECK GATE PASSED: safe to forecast.")
    else:
        print(f"DATA CHECK GATE FAILED ({len(gate.failures)} problem(s)): run stopped, nothing forecast.")
    sys.exit(0 if gate.passed else 1)
