"""Zoho adapters: mock (default) or live Zoho Inventory when ZOHO_MODE=live."""

from planner.config import Config, env
from planner.zoho.base import ZohoAdapter


def get_zoho_adapter(cfg: Config | None = None, db_url: str | None = None) -> ZohoAdapter:
    if env("ZOHO_MODE", "mock").lower() == "live":
        from planner.zoho.inventory_adapter import ZohoInventoryAdapter
        return ZohoInventoryAdapter()
    from planner.zoho.mock_adapter import MockZohoAdapter
    return MockZohoAdapter(cfg, db_url)
