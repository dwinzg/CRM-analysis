"""Writeback tests against a fake CRM.

The load-bearing assertion in this file is that a CHOW never patches
parent_id on the old account. Everything else is guard rails around it.
"""
import json

import pytest

from bellhaven import config
from bellhaven.apply import apply_approved, apply_one
from bellhaven.classify import Proposal
from bellhaven.ledger import Ledger, fingerprint

BH = config.BELLHAVEN_PARENT_ID

OLD = {
    "account_id": "OLD1", "name": "Bellhaven of Tiffin", "parent_id": "CEDAR",
    "parent_name": "Cedar Trail Communities (Parent Account)",
    "lifetime_revenue": 84000, "outstanding_ar": 12400, "note": "",
    "chow_current_account": "", "duplicate_of_account": "",
    "billing_street": "45 St Lawrence Dr", "billing_city": "Tiffin",
    "billing_state": "OH", "billing_zip": "44883",
    "care_type": "Skilled Nursing", "phone": "", "status": "Active",
}


class FakeCrm:
    """Mirrors CrmClient's surface, including that PATCH returns an ack only."""

    def __init__(self, accounts):
        self.accounts = {a["account_id"]: dict(a) for a in accounts}
        self.patches = []
        self.creates = []

    def get_account(self, account_id):
        return dict(self.accounts[account_id])

    def patch_account(self, account_id, fields):
        self.patches.append((account_id, dict(fields)))
        self.accounts[account_id].update(fields)
        return {"account_id": account_id, "message": "updated", "fields": list(fields)}

    def create_account(self, fields):
        new_id = f"NEW{len(self.creates) + 1}"
        self.creates.append(dict(fields))
        self.accounts[new_id] = {**fields, "account_id": new_id}
        return {"account_id": new_id, "message": "created"}


@pytest.fixture
def led(tmp_path):
    return Ledger(str(tmp_path / "t.db"))


def approve(led, prop):
    led.upsert([prop])
    fp = fingerprint(prop.kind, prop.target_account_id, prop.changes, prop.new_account)
    led.decide(fp, "approved", "")
    return fp


def row_for(led, fp):
    return next(r for r in led.all_rows() if r["fingerprint"] == fp)


# --- the SOP -------------------------------------------------------------

def test_chow_creates_successor_and_never_patches_old_parent(led):
    p = Proposal("CHOW", "OLD1", "bellhaven-of-tiffin",
                 {"chow_current_account": "<successor>", "note": "chow"},
                 {}, 100,
                 {"name": "Bellhaven of Tiffin", "parent_id": BH, "status": "Active"})
    approve(led, p)
    crm = FakeCrm([OLD])
    apply_approved(crm, led)

    assert len(crm.creates) == 1
    assert crm.creates[0]["parent_id"] == BH

    assert len(crm.patches) == 1
    account_id, fields = crm.patches[0]
    assert account_id == "OLD1"
    assert "parent_id" not in fields, "SOP violation: old account was re-parented"
    assert fields["chow_current_account"] == "NEW1"


def test_chow_preserves_every_business_field_on_the_old_account(led):
    p = Proposal("CHOW", "OLD1", "s", {"chow_current_account": "<successor>", "note": "n"},
                 {}, 100, {"name": "X", "parent_id": BH})
    approve(led, p)
    crm = FakeCrm([OLD])
    apply_approved(crm, led)
    after = crm.accounts["OLD1"]
    for field in ("name", "parent_id", "status", "lifetime_revenue", "outstanding_ar",
                  "billing_street", "billing_zip", "care_type"):
        assert after[field] == OLD[field], f"CHOW changed {field}"


def test_chow_aborts_if_target_already_moved(led):
    """If the account is already under Bellhaven, the CHOW is stale — and
    creating a successor anyway would duplicate the facility."""
    p = Proposal("CHOW", "OLD1", "s", {"chow_current_account": "<successor>", "note": "n"},
                 {}, 100, {"name": "X", "parent_id": BH})
    fp = approve(led, p)
    crm = FakeCrm([{**OLD, "parent_id": BH}])
    apply_approved(crm, led)
    assert crm.creates == [] and crm.patches == []
    assert row_for(led, fp)["status"] == "failed"


# --- ordinary writes -----------------------------------------------------

def test_reparent_patches_parent_only(led):
    p = Proposal("REPARENT", "OLD1", "s", {"parent_id": BH, "note": "n"}, {}, 100)
    approve(led, p)
    crm = FakeCrm([OLD])
    apply_approved(crm, led)
    assert crm.patches[0][1]["parent_id"] == BH
    assert crm.creates == []


def test_create_stores_new_id_in_the_ledger(led):
    p = Proposal("CREATE", "", "amberly-manor", {}, {}, 100,
                 {"name": "Amberly Manor", "parent_id": BH})
    fp = approve(led, p)
    crm = FakeCrm([OLD])
    apply_approved(crm, led)
    row = row_for(led, fp)
    assert row["status"] == "applied"
    assert json.loads(row["result_json"])["created_account_id"] == "NEW1"


def test_duplicate_marks_loser(led):
    loser = {**OLD, "account_id": "DUP1", "lifetime_revenue": 0, "outstanding_ar": 0}
    p = Proposal("DUPLICATE", "DUP1", "s",
                 {"duplicate_of_account": "OLD1", "status": "Inactive", "note": "n"}, {}, 100)
    approve(led, p)
    crm = FakeCrm([OLD, loser])
    apply_approved(crm, led)
    _, fields = crm.patches[0]
    assert fields["duplicate_of_account"] == "OLD1"
    assert fields["status"] == "Inactive"


# --- the approval gate ---------------------------------------------------

def test_pending_is_never_written(led):
    led.upsert([Proposal("RENAME", "OLD1", "s", {"name": "Nope"}, {}, 100)])
    crm = FakeCrm([OLD])
    apply_approved(crm, led)
    assert crm.patches == [] and crm.creates == []


def test_rejected_is_never_written(led):
    p = Proposal("RENAME", "OLD1", "s", {"name": "Nope"}, {}, 100)
    led.upsert([p])
    led.decide(fingerprint(p.kind, p.target_account_id, p.changes, None), "rejected", "")
    crm = FakeCrm([OLD])
    apply_approved(crm, led)
    assert crm.patches == [] and crm.creates == []


def test_applied_row_not_reapplied(led):
    p = Proposal("RENAME", "OLD1", "s", {"name": "Bellhaven of Tiffin X"}, {}, 100)
    approve(led, p)
    crm = FakeCrm([OLD])
    apply_approved(crm, led)
    apply_approved(crm, led)
    assert len(crm.patches) == 1


# --- staleness -----------------------------------------------------------

def test_stale_target_marked_failed_not_applied(led):
    """The CRM moved between propose and apply. Never blind-overwrite."""
    p = Proposal("REPARENT", "OLD1", "s", {"parent_id": BH, "note": "n"}, {}, 100)
    fp = approve(led, p)
    crm = FakeCrm([{**OLD, "parent_id": BH}])
    apply_approved(crm, led)
    assert row_for(led, fp)["status"] == "failed"
    assert crm.patches == []


def test_one_failure_does_not_abort_the_rest(led):
    stale = Proposal("REPARENT", "OLD1", "s", {"parent_id": BH, "note": "n"}, {}, 100)
    good = Proposal("RENAME", "OTHER", "s", {"name": "Renamed"}, {}, 100)
    fp_stale = approve(led, stale)
    fp_good = approve(led, good)
    crm = FakeCrm([{**OLD, "parent_id": BH}, {**OLD, "account_id": "OTHER"}])
    results = apply_approved(crm, led)
    assert len(results) == 2
    assert row_for(led, fp_stale)["status"] == "failed"
    assert row_for(led, fp_good)["status"] == "applied"


def test_note_alone_is_not_treated_as_stale(led):
    """A proposal whose only non-note field already matches is stale, but a
    matching note must not by itself block a real change."""
    p = Proposal("RENAME", "OLD1", "s", {"name": "Renamed", "note": ""}, {}, 100)
    fp = approve(led, p)
    crm = FakeCrm([OLD])
    apply_approved(crm, led)
    assert row_for(led, fp)["status"] == "applied"


def test_apply_one_rejects_unknown_kind():
    with pytest.raises(ValueError):
        apply_one(FakeCrm([OLD]), {"kind": "NONSENSE", "changes_json": "{}",
                                   "new_account_json": "null", "target_account_id": "OLD1"})
