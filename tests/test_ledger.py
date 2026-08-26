"""Ledger tests: the machinery that makes a daily re-run safe."""
import json

import pytest

from bellhaven.classify import Proposal
from bellhaven.ledger import Ledger, fingerprint


_DEFAULT = object()


def mk(kind="RENAME", tid="A1", changes=_DEFAULT, new=None, slug="slug"):
    # Sentinel, not `changes or {...}`: a CREATE legitimately has empty changes,
    # and an empty dict is falsy.
    if changes is _DEFAULT:
        changes = {"name": "New Name"}
    return Proposal(kind, tid, slug, changes, {"signals": []}, 100, new)


@pytest.fixture
def led(tmp_path):
    return Ledger(str(tmp_path / "t.db"))


def fp_of(p):
    return fingerprint(p.kind, p.target_account_id, p.changes, p.new_account)


# --- fingerprint identity ------------------------------------------------

def test_fingerprint_ignores_note():
    """Notes carry dates. If the date is inside the hash, a rejected proposal
    comes back with a new identity every single day, forever."""
    a = mk(changes={"name": "X", "note": "not listed as of 2026-08-26"})
    b = mk(changes={"name": "X", "note": "not listed as of 2026-09-01"})
    assert fp_of(a) == fp_of(b)


def test_fingerprint_changes_with_real_values():
    assert fp_of(mk(changes={"name": "X"})) != fp_of(mk(changes={"name": "Y"}))


def test_fingerprint_distinguishes_kind_and_target():
    assert fp_of(mk(kind="RENAME")) != fp_of(mk(kind="FIELD_FIX"))
    assert fp_of(mk(tid="A1")) != fp_of(mk(tid="A2"))


def test_fingerprint_covers_the_new_account_payload():
    """Two CREATEs for different facilities must not collide."""
    a = mk(kind="CREATE", tid="", changes={}, new={"name": "Amberly Manor"})
    b = mk(kind="CREATE", tid="", changes={}, new={"name": "Bellhaven of Batavia"})
    assert fp_of(a) != fp_of(b)


def test_fingerprint_is_stable_across_key_order():
    a = mk(changes={"name": "X", "billing_zip": "1"})
    b = mk(changes={"billing_zip": "1", "name": "X"})
    assert fp_of(a) == fp_of(b)


# --- re-run behaviour ----------------------------------------------------

def test_second_run_inserts_nothing(led):
    props = [mk(), mk(tid="A2")]
    assert led.upsert(props)["inserted"] == 2
    assert led.upsert(props)["inserted"] == 0
    assert len(led.pending()) == 2


def test_rejected_stays_rejected(led):
    """The requirement in the brief: a decided item never comes back."""
    p = mk()
    led.upsert([p])
    led.decide(fp_of(p), "rejected", "not a real change")
    led.upsert([p])
    assert led.pending() == []
    assert led.all_rows()[0]["status"] == "rejected"


def test_approved_survives_rerun(led):
    p = mk()
    led.upsert([p])
    led.decide(fp_of(p), "approved", "")
    led.upsert([p])
    assert [r["fingerprint"] for r in led.approved()] == [fp_of(p)]


def test_applied_is_never_reoffered(led):
    p = mk()
    led.upsert([p])
    led.decide(fp_of(p), "approved", "")
    led.mark_applied(fp_of(p), {"patched": ["name"]})
    led.upsert([p])
    assert led.pending() == []
    assert led.approved() == []


def test_changed_payload_creates_new_row_and_stales_old(led):
    led.upsert([mk(changes={"name": "X"})])
    led.upsert([mk(changes={"name": "Y"})])
    rows = {r["status"] for r in led.all_rows()}
    assert rows == {"stale", "pending"}
    assert len(led.all_rows()) == 2


def test_stale_only_affects_pending(led):
    """A decision is permanent. Disappearing from a run must not un-decide it."""
    keep, gone = mk(tid="KEEP"), mk(tid="GONE")
    led.upsert([keep, gone])
    led.decide(fp_of(gone), "approved", "")
    led.upsert([keep])
    by_fp = {r["fingerprint"]: r for r in led.all_rows()}
    assert by_fp[fp_of(gone)]["status"] == "approved"
    assert by_fp[fp_of(keep)]["status"] == "pending"


def test_reappearing_proposal_is_not_duplicated(led):
    p = mk()
    led.upsert([p])
    led.upsert([])              # vanishes -> stale
    assert led.all_rows()[0]["status"] == "stale"
    led.upsert([p])             # comes back
    assert len(led.all_rows()) == 1


def test_mark_applied_records_result(led):
    p = mk()
    led.upsert([p])
    led.decide(fp_of(p), "approved", "")
    led.mark_applied(fp_of(p), {"created_account_id": "NEW1"})
    row = led.all_rows()[0]
    assert row["status"] == "applied"
    assert json.loads(row["result_json"])["created_account_id"] == "NEW1"


def test_mark_failed_records_reason(led):
    p = mk()
    led.upsert([p])
    led.decide(fp_of(p), "approved", "")
    led.mark_failed(fp_of(p), "target moved")
    row = led.all_rows()[0]
    assert row["status"] == "failed"
    assert json.loads(row["result_json"])["error"] == "target moved"


def test_decide_rejects_unknown_status(led):
    led.upsert([mk()])
    with pytest.raises(ValueError):
        led.decide(led.pending()[0]["fingerprint"], "maybe", "")


def test_payload_round_trips(led):
    p = mk(kind="CREATE", tid="", changes={}, new={"name": "Amberly Manor", "parent_id": "P"})
    led.upsert([p])
    row = led.pending()[0]
    assert json.loads(row["new_account_json"])["name"] == "Amberly Manor"
    assert json.loads(row["changes_json"]) == {}
