"""The only module that talks to the CRM API.

Deliberately thin: no business logic, no retries beyond httpx defaults, no
caching. Everything interesting happens in the pure modules, which is what
lets the test suite run offline.
"""
from __future__ import annotations

import httpx

from . import config


class CrmClient:
    def __init__(self, token: str | None = None, base: str = config.API_BASE):
        self.base = base
        self._c = httpx.Client(
            headers={"Authorization": f"Bearer {token or config.token()}"},
            timeout=30.0,
        )

    def list_accounts(self) -> list[dict]:
        """Every account, following pagination to the end."""
        out: list[dict] = []
        page = 1
        while True:
            r = self._c.get(f"{self.base}/accounts", params={"page": page, "page_size": 200})
            r.raise_for_status()
            body = r.json()
            batch = body.get("data", [])
            out.extend(batch)
            if not batch or len(out) >= body.get("total", len(out)):
                return out
            page += 1

    def get_account(self, account_id: str) -> dict:
        r = self._c.get(f"{self.base}/accounts/{account_id}")
        r.raise_for_status()
        return r.json()

    def patch_account(self, account_id: str, fields: dict) -> dict:
        r = self._c.patch(f"{self.base}/accounts/{account_id}", json=fields)
        r.raise_for_status()
        return r.json()

    def create_account(self, fields: dict) -> dict:
        r = self._c.post(f"{self.base}/accounts", json=fields)
        r.raise_for_status()
        return r.json()


def account_id_of(payload: dict) -> str:
    """Pull the id out of a create/update response.

    The API's OpenAPI spec declares no response schema, so the field name is not
    contractually guaranteed. GET returns `account_id`; this accepts the obvious
    alternatives rather than failing mid-writeback.
    """
    for key in ("account_id", "id", "accountId"):
        if payload.get(key):
            return payload[key]
    raise KeyError(f"no account id in response: {sorted(payload)}")
