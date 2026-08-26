"""Writes approved proposals to the CRM. The only module that mutates data.

Two invariants:

1. It reads `ledger.approved()` and nothing else. A pending or rejected
   proposal is unreachable from here, which is what "nothing writes without
   approval" means in practice.
2. It re-reads the target before every write and compares each field against
   the value recorded when the proposal was raised. If the CRM has moved
   since, the row is marked failed rather than overwriting someone else's
   edit. A failed row is not a verdict on the change: the next pipeline run
   offers it again.
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

    before = json.loads(row.get("evidence_json") or "{}").get("before", {})

    if kind == "CREATE":
        # Without this, a replayed approval makes a second account for one
        # facility. There is no delete in this API, so that is expensive.
        for existing in client.list_accounts():
            if (existing.get("name") == new_account.get("name")
                    and existing.get("billing_zip") == new_account.get("billing_zip")):
                raise ValueError(
                    f"account named {new_account['name']!r} already exists at that zip "
                    f"({existing['account_id']}); CREATE proposal is stale")
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

    # Ordinary field writes, guarded two ways.
    substantive = {k: v for k, v in changes.items() if k != "note"}

    # 1. The work is already done.
    if substantive and all(current.get(k) == v for k, v in substantive.items()):
        already = ", ".join(sorted(substantive))
        raise ValueError(f"already at proposed value ({already}); proposal is stale")

    # 2. Someone else changed the field since the proposal was raised. Applying
    #    now would silently destroy their edit, so refuse and let a human look.
    for field in substantive:
        if field in before and current.get(field, "") != before[field]:
            raise ValueError(
                f"{field} changed since this was proposed "
                f"(expected {before[field]!r}, found {current.get(field)!r}); "
                f"re-run the pipeline to raise a fresh proposal")

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
