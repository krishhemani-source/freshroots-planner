"""Upload the export files in data/ to the hosted (Neon) database, exactly as they are.

A file missing locally is removed online too, so the corrupted-data demo works on the hosted
site: run scripts/corrupt_data.py, then this script, then press Run now on the site.

Usage:  python scripts/upload_exports.py          (needs DEPLOY_DATABASE_URL in .env)
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from planner.config import env, load_config  # noqa: E402
from planner.datastore import upload_exports  # noqa: E402

if __name__ == "__main__":
    url = env("DEPLOY_DATABASE_URL")
    if not url:
        sys.exit("Set DEPLOY_DATABASE_URL in .env to the Neon connection string first.")
    result = upload_exports(load_config(), url)
    for name, size in result["uploaded"].items():
        print(f"  uploaded {name:24} {size / 1e6:6.1f} MB")
    for name in result["removed"]:
        print(f"  removed  {name:24} (missing locally)")
