"""Live Zoho Inventory adapter (ZOHO_MODE=live). Creates purchase orders in DRAFT status only:
it never calls Zoho's "mark as issued" endpoint, so procurement still approves every order in Zoho.

Needs in .env: ZOHO_CLIENT_ID, ZOHO_CLIENT_SECRET, ZOHO_REFRESH_TOKEN, ZOHO_ORG_ID, ZOHO_REGION
(in = India data centre, com = US, eu, ...). Not tested against a real Zoho account; it needs the
SKUs and vendors to exist in Zoho with matching SKU codes and vendor names.
"""

import time

import requests

from planner.config import env
from planner.zoho.base import ZohoAdapter


class ZohoInventoryAdapter(ZohoAdapter):
    def __init__(self, session: requests.Session | None = None):
        missing = [k for k in ["ZOHO_CLIENT_ID", "ZOHO_CLIENT_SECRET", "ZOHO_REFRESH_TOKEN", "ZOHO_ORG_ID"] if not env(k)]
        if missing:
            raise RuntimeError(f"ZOHO_MODE=live needs {', '.join(missing)} in .env")
        region = env("ZOHO_REGION", "in")
        self.accounts_url = f"https://accounts.zoho.{region}/oauth/v2/token"
        self.api_url = f"https://www.zohoapis.{region}/inventory/v1"
        self.org_id = env("ZOHO_ORG_ID")
        self.http = session or requests.Session()
        self._token, self._token_expiry = None, 0.0

    def _access_token(self) -> str:
        if self._token is None or time.time() > self._token_expiry - 60:
            r = self.http.post(self.accounts_url, params={
                "refresh_token": env("ZOHO_REFRESH_TOKEN"), "client_id": env("ZOHO_CLIENT_ID"),
                "client_secret": env("ZOHO_CLIENT_SECRET"), "grant_type": "refresh_token"}, timeout=30)
            r.raise_for_status()
            body = r.json()
            self._token, self._token_expiry = body["access_token"], time.time() + int(body.get("expires_in", 3600))
        return self._token

    def _call(self, method: str, path: str, **kwargs) -> dict:
        r = self.http.request(method, f"{self.api_url}{path}", timeout=30,
                              params={"organization_id": self.org_id, **kwargs.pop("params", {})},
                              headers={"Authorization": f"Zoho-oauthtoken {self._access_token()}"}, **kwargs)
        r.raise_for_status()
        return r.json()

    def _find_id(self, path: str, key: str, params: dict, label: str) -> str:
        items = self._call("GET", path, params=params).get(key, [])
        if not items:
            raise LookupError(f"{label} not found in Zoho: {params}")
        return items[0][key[:-1] + "_id"]          # "items" -> "item_id", "contacts" -> "contact_id"

    def create_draft_po(self, po: dict) -> str:
        item_id = self._find_id("/items", "items", {"sku": po["sku_id"]}, "Item")
        vendor_id = self._find_id("/contacts", "contacts", {"contact_name": po["vendor_name"],
                                                            "contact_type": "vendor"}, "Vendor")
        body = {"vendor_id": vendor_id, "notes": po["reason"][:1000],
                "line_items": [{"item_id": item_id, "quantity": po["qty"], "rate": po["unit_cost_inr"]}]}
        created = self._call("POST", "/purchaseorders", json=body)   # created as draft; never issued here
        return created["purchaseorder"]["purchaseorder_number"]
