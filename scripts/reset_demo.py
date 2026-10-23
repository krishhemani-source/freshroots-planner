"""Get ready for a demo: fresh synthetic data dated today, empty review queue, kill switch ACTIVE.

Uses the dataset chosen by DATASET in .env: synthetic (default) or case (professor's data).

Usage:
  python scripts/reset_demo.py                 # laptop (SQLite)
  python scripts/reset_demo.py --hosted        # also reset Neon and upload the fresh exports
                                               # (needs DEPLOY_DATABASE_URL in .env)
"""

import argparse
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from planner.case_data import import_case_data  # noqa: E402
from planner.config import env, load_config  # noqa: E402
from planner.datastore import upload_exports  # noqa: E402
from planner.demo import reset_demo_state  # noqa: E402
from planner.synthetic_data import generate  # noqa: E402

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--hosted", action="store_true", help="also reset the hosted (Neon) database")
    args = parser.parse_args()

    cfg = load_config()
    if env("DATASET", "synthetic").lower() == "case":
        print("Importing the professor's case data (prof_data/ -> data_case/)...")
        import_case_data(cfg)
    else:
        print("Generating fresh synthetic exports dated today...")
        generate(cfg)
    print("Local database:", reset_demo_state(cfg, date.today())["removed"])
    if args.hosted:
        url = env("DEPLOY_DATABASE_URL")
        if not url:
            sys.exit("Set DEPLOY_DATABASE_URL in .env to the Neon connection string first.")
        print("Hosted database:", reset_demo_state(cfg, date.today(), url)["removed"])
        print("Uploaded:", ", ".join(upload_exports(cfg, url)["uploaded"]))
    print("Ready. Start the app and press Run now.")
