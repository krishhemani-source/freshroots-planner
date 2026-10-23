"""Start the scheduler: runs planning Mon & Thu 06:00 IST (see config.yaml). Leave it running.

Usage:  python scripts/scheduler.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from planner.pipeline import setup_logging  # noqa: E402
from planner.scheduler import start  # noqa: E402

if __name__ == "__main__":
    setup_logging()
    start()
