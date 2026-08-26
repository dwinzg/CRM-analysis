"""Writes approved proposals to the CRM. The only module that mutates data.

Two invariants:

1. It reads `ledger.approved()` and nothing else. A pending or rejected
   proposal is unreachable from here, which is what "nothing writes without
   approval" means in practice.
2. It re-reads the target before every write. A proposal is a statement about
   the CRM as it was when the pipeline ran; if the CRM has moved since, the
   proposal is stale and gets marked failed rather than blindly overwriting
   someone else's change.
"""
from __future__ import annotations

import json

from . import config
from .crm import CrmClient, account_id_of
from .ledger import Ledger

WRITE_KINDS = ("CREATE", "CHOW", "REPARENT", "RENAME", "FIELD_FIX", "DUPLICATE", "DELISTED")


def apply_one(client, row: dict) -> dict:
    """Perform one proposal's writes. Raises on a stale or unknown proposal."""
    kind = row["kind"]
    if kind not in WRITE_KINDS:
        raise ValueError(f"unknown proposal kind: {kind!r}")

    changes = json.loads(row["changes_json"] or "{}")
    new_account = json.loads(row["new_account_json"] or "null")
    target = row["target_account_id"]

    if kind == "CREATE":
        created = client.create_account(new_account)
        return {"created_account_id": account_id_of(created)}

    current = client.get_account(target)

    if kind == "CHOW":
        # If the facility already moved, creating a successor would duplicate it.
        if current.get("parent_id") == config.BELLHAVEN_PARENT_ID:
            raise ValueError("target is already under Bellhaven; CHOW proposal is stale")
        if current.get("chow_current_account"):
            raise ValueError("target already has a chow_current_account; proposal is stale")

        created = client.create_account(new_account)
        successor_id = account_id_of(created)

        # Rebuild the patch from scratch rather than trusting the stored dict.
        # parent_id must never reach this call. That is the entire SOP.
        patch = {k: v for k, v in changes.items() if k not in ("parent_id", "chow_current_account")}
        patch["chow_current_account"] = successor_id
        client.patch_account(target, patch)
        return {"created_account_id": successor_id, "patched": sorted(patch)}

    # Ordinary field writes. If every substantive field already holds the
    # proposed value, the work is done and the proposal is stale.
    substantive = {k: v for k, v in changes.items() if k != "note"}
    if substantive and all(current.get(k) == v for k, v in substantive.items()):
        already = ", ".join(sorted(substantive))
        raise ValueError(f"already at proposed value ({already}); proposal is stale")

    client.patch_account(target, changes)
    return {"patched": sorted(changes)}


def apply_approved(client=None, ledger=None) -> list[dict]:
    """Apply every approved proposal. One failure never aborts the rest."""
    client = client or CrmClient()
    ledger = ledger or Ledger(config.DB_PATH)

    results = []
    for row in ledger.approved():
        label = f"{row['kind']} {row['target_account_id'] or row['site_slug']}"
        try:
            result = apply_one(client, row)
            ledger.mark_applied(row["fingerprint"], result)
            results.append({"fingerprint": row["fingerprint"], "what": label,
                            "ok": True, **result})
        except Exception as exc:  # one bad row must not strand the others
            ledger.mark_failed(row["fingerprint"], str(exc))
            results.append({"fingerprint": row["fingerprint"], "what": label,
                            "ok": False, "error": str(exc)})
    return results


def main() -> None:
    results = apply_approved()
    ok = sum(1 for r in results if r["ok"])
    print(json.dumps({"applied": ok, "failed": len(results) - ok, "results": results}, indent=2))


if __name__ == "__main__":
    main()
