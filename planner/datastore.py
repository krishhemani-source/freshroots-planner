"""Where the export files come from.

DATA_SOURCE=files (default, laptop): read them from the data/ folder.
DATA_SOURCE=db (Vercel): the files are stored in the database (uploaded from the laptop with
scripts/upload_exports.py) and copied to a temporary folder at the start of each run.
"""

import gzip
import os
import shutil
import tempfile
from pathlib import Path

from sqlalchemy import delete, select

from planner.config import Config, env, now_ist
from planner.db import ExportFile, get_sessionmaker


def export_file_names(cfg: Config) -> list[str]:
    return list(cfg.data.files.values()) + [cfg.data.manifest_file]


def export_folder(cfg: Config, db_url: str | None = None) -> Path:
    """Folder holding the export files for this run."""
    if env("DATA_SOURCE", "files").lower() != "db":
        return cfg.data.folder
    target = Path(tempfile.gettempdir()) / "freshroots-exports"
    shutil.rmtree(target, ignore_errors=True)
    target.mkdir(parents=True)
    with get_sessionmaker(db_url)() as s:
        for f in s.scalars(select(ExportFile)):
            path = target / f.file_name
            path.write_bytes(gzip.decompress(f.content_gzip))
            stamp = f.uploaded_at.timestamp()
            os.utime(path, (stamp, stamp))   # so a missing manifest falls back to upload time, not "now"
    return target


def upload_exports(cfg: Config, db_url: str | None = None, folder: Path | None = None) -> dict:
    """Copy the local export files into the database. A file missing locally is removed there too,
    so a corrupted local folder is reproduced exactly (useful for the data-gate demo)."""
    folder = Path(folder or cfg.data.folder)
    uploaded, removed = {}, []
    with get_sessionmaker(db_url)() as s:
        for name in export_file_names(cfg):
            path = folder / name
            if not path.exists():
                s.execute(delete(ExportFile).where(ExportFile.file_name == name))
                removed.append(name)
                continue
            raw = path.read_bytes()
            s.merge(ExportFile(file_name=name, content_gzip=gzip.compress(raw, compresslevel=6), size_bytes=len(raw),
                               uploaded_at=now_ist().replace(tzinfo=None)))
            uploaded[name] = len(raw)
        s.commit()
    return {"uploaded": uploaded, "removed": removed}
