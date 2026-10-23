"""Entry point for the Python API (Vercel Python function locally run with uvicorn).

Local:  .venv/bin/uvicorn api.index:app --reload --port 8000
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from planner.api import create_app  # noqa: E402
from planner.pipeline import setup_logging  # noqa: E402

setup_logging()
app = create_app()
