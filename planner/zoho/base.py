"""What any Zoho adapter must provide (mock by default; live Zoho Inventory when ZOHO_MODE=live)."""

from abc import ABC, abstractmethod


class ZohoAdapter(ABC):
    @abstractmethod
    def create_draft_po(self, po: dict) -> str:
        """Create one purchase order in DRAFT status (never issued). Returns the Zoho PO id."""
