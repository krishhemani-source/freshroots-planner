"""Mock Zoho (default, ZOHO_MODE=mock): reads open POs from zoho_open_pos.csv and writes
draft POs to the local database instead of Zoho."""

import pandas as pd
from sqlalchemy import func, select

from planner.config import Config, load_config, now_ist
from planner.db import MockZohoPO, get_sessionmaker
from planner.zoho.base import ZohoAdapter


class MockZohoAdapter(ZohoAdapter):
    def __init__(self, cfg: Config | None = None, db_url: str | None = None):
        self.cfg = cfg or load_config()
        self.db_url = db_url

    def list_open_pos(self) -> pd.DataFrame:
        return pd.read_csv(self.cfg.data.path("zoho_open_pos"), dtype={"sku_id": str})

    def create_draft_po(self, po: dict) -> str:
        with get_sessionmaker(self.db_url)() as s:
            n = (s.scalar(select(func.count()).select_from(MockZohoPO)) or 0) + 1
            number = f"MOCK-PO-{n:05d}"
            s.add(MockZohoPO(po_number=number, status="draft", payload=po, created_at=now_ist().replace(tzinfo=None)))
            s.commit()
        return number
