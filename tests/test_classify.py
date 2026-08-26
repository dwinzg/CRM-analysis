"""Classifier tests. This is where the billing SOP is pinned down.

Fixture-driven and offline. Every id and figure below is real data from this
CRM copy.
"""
import json

import pytest

from bellhaven import config
from bellhaven.classify import build_proposals, needs_chow, pick_survivor
from bellhaven.match import match_all
from bellhaven.scrape import SiteLocation

BH = config.BELLHAVEN_PARENT_ID


@pytest.fixture(scope="module")
def sites():
    return [SiteLocation(**d) for d in json.load(open("tests/fixtures/site.json"))]


@pytest.fixture(scope="module")
def accounts():
    return json.load(open("tests/fixtures/accounts.json"))


@pytest.fixture(scope="module")
def props(sites, accounts):
    return build_proposals(sites, accounts, match_all(sites, accounts))


def of_kind(props, kind):
    return [p for p in props if p.kind == kind]


def by_name(accounts, name):
    return next(a for a in accounts if a["name"] == name)


def aid(accounts, name):
    return by_name(accounts, name)["account_id"]


# --- the SOP -------------------------------------------------------------

@pytest.mark.parametrize("rev,ar,expected,label", [
    (51250, 3800, True, "Marietta"),
    (84000, 12400, True, "Tiffin"),
    (47000, 0, False, "Lima: revenue but no AR"),
    (22000, 0, False, "Findlay: revenue but no AR"),
    (0, 5000, False, "AR but no revenue"),
    (0, 0, False, "neither"),
])
def test_chow_truth_table(rev, ar, expected, label):
    """The AND is load-bearing. An `or` here invents two bogus accounts."""
    assert needs_chow({"lifetime_revenue": rev, "outstanding_ar": ar}) is expected, label


def test_marietta_and_tiffin_are_chow(props, accounts):
    ids = {p.target_account_id for p in of_kind(props, "CHOW")}
    assert aid(accounts, "Bellhaven of Marietta") in ids
    assert aid(accounts, "Bellhaven of Tiffin") in ids
    assert len(ids) == 2, "exactly two accounts in this CRM satisfy revenue AND AR"


def test_lima_and_findlay_are_plain_reparents(props, accounts):
    """Both carry real revenue. Both have zero AR, so the SOP says re-parent
    in place — this is the easiest case in the exercise to get wrong."""
    reparents = {p.target_account_id for p in of_kind(props, "REPARENT")}
    chows = {p.target_account_id for p in of_kind(props, "CHOW")}
    for name in ("Bellhaven Crossings of Lima", "Bellhaven Meadows of Findlay"):
        assert aid(accounts, name) in reparents, name
        assert aid(accounts, name) not in chows, name


def test_chow_proposal_never_changes_parent(props):
    for p in of_kind(props, "CHOW"):
        assert "parent_id" not in p.changes
        assert set(p.changes) == {"chow_current_account", "note"}
        assert p.new_account is not None
        assert p.new_account["parent_id"] == BH


def test_chow_target_gets_no_other_proposals(props, accounts):
    """'Leave the existing account exactly as it is.' No rename, no field fix."""
    chow_ids = {p.target_account_id for p in of_kind(props, "CHOW")}
    for p in props:
        if p.target_account_id in chow_ids:
            assert p.kind == "CHOW", f"{p.kind} also targets a CHOW account"


def test_reparent_targets_are_all_currently_wrong(props, accounts):
    by_id = {a["account_id"]: a for a in accounts}
    for p in of_kind(props, "REPARENT"):
        assert by_id[p.target_account_id]["parent_id"] != BH
        assert p.changes["parent_id"] == BH


# --- duplicates ----------------------------------------------------------

def test_kettering_trio_one_survivor_two_losers(props, accounts):
    ids = {aid(accounts, n) for n in ("Kettering Care Centre", "Kettering Senior Campus",
                                      "Kettering Nursing & Rehabilitation")}
    dupes = [p for p in of_kind(props, "DUPLICATE") if p.target_account_id in ids]
    assert len(dupes) == 2
    survivors = {p.changes["duplicate_of_account"] for p in dupes}
    assert len(survivors) == 1
    survivor = survivors.pop()
    assert survivor in ids
    assert survivor not in {p.target_account_id for p in dupes}
    for p in dupes:
        assert p.changes["status"] == "Inactive"
        assert p.changes["duplicate_of_account"] == survivor


def test_monroe_survivor_is_the_account_already_under_bellhaven(props, accounts):
    keep = aid(accounts, "Bellhaven Gardens of Monroe")
    losers = {aid(accounts, "Cedar Trail of Monroe"),
              aid(accounts, "Monroe Gardens Care Center")}
    dupes = [p for p in of_kind(props, "DUPLICATE") if p.target_account_id in losers]
    assert len(dupes) == 2
    assert {p.changes["duplicate_of_account"] for p in dupes} == {keep}


def test_owosso_pair_survivor_not_marked_duplicate_of_itself(props, accounts):
    ids = [a["account_id"] for a in accounts if a["name"] == "Bellhaven of Owosso"]
    assert len(ids) == 2
    dupes = [p for p in of_kind(props, "DUPLICATE") if p.target_account_id in ids]
    assert len(dupes) == 1
    assert dupes[0].changes["duplicate_of_account"] != dupes[0].target_account_id
    assert dupes[0].changes["duplicate_of_account"] in ids


def test_survivor_prefers_bellhaven_then_revenue():
    a = {"account_id": "A", "parent_id": "other", "lifetime_revenue": 999, "outstanding_ar": 0}
    b = {"account_id": "B", "parent_id": BH, "lifetime_revenue": 0, "outstanding_ar": 0}
    assert pick_survivor([a, b])["account_id"] == "B", "Bellhaven beats revenue"

    c = {"account_id": "C", "parent_id": "other", "lifetime_revenue": 10, "outstanding_ar": 0}
    d = {"account_id": "D", "parent_id": "other", "lifetime_revenue": 500, "outstanding_ar": 0}
    assert pick_survivor([c, d])["account_id"] == "D", "revenue breaks the tie"

    e = {"account_id": "Z", "parent_id": "", "lifetime_revenue": 0, "outstanding_ar": 0}
    f = {"account_id": "A", "parent_id": "", "lifetime_revenue": 0, "outstanding_ar": 0}
    assert pick_survivor([e, f])["account_id"] == "A", "deterministic id tiebreak"


def test_survivor_prefers_lineage_over_an_orphan():
    """All else equal, a record with an acquisition trail beats a parentless
    one. This is what decides the Kettering trio, where revenue, AR and
    contacts are all zero across the board."""
    orphan = {"account_id": "A", "parent_id": "", "lifetime_revenue": 0, "outstanding_ar": 0}
    lineage = {"account_id": "Z", "parent_id": "HARBORVIEW", "lifetime_revenue": 0,
               "outstanding_ar": 0}
    assert pick_survivor([orphan, lineage])["account_id"] == "Z"


def test_revenue_outranks_lineage():
    """Lineage must never outrank a billing signal — losing a billed record
    has real cost, losing a provenance trail does not."""
    orphan_with_money = {"account_id": "A", "parent_id": "", "lifetime_revenue": 90000,
                         "outstanding_ar": 0}
    empty_with_lineage = {"account_id": "B", "parent_id": "CEDAR", "lifetime_revenue": 0,
                          "outstanding_ar": 0}
    assert pick_survivor([orphan_with_money, empty_with_lineage])["account_id"] == "A"


def test_no_account_is_both_survivor_and_loser(props):
    losers = {p.target_account_id for p in of_kind(props, "DUPLICATE")}
    survivors = {p.changes["duplicate_of_account"] for p in of_kind(props, "DUPLICATE")}
    assert not (losers & survivors)


def test_duplicate_losers_get_no_other_proposals(props):
    """A record being retired shouldn't also be renamed or re-parented."""
    losers = {p.target_account_id for p in of_kind(props, "DUPLICATE")}
    for p in props:
        if p.target_account_id in losers:
            assert p.kind == "DUPLICATE", f"{p.kind} also targets retired account"


# --- renames, field fixes, creates --------------------------------------

def test_renames_proposed(props, accounts):
    renames = {p.target_account_id: p.changes["name"] for p in of_kind(props, "RENAME")}
    expected = {
        "Sunny Acres Retirement Home": "Bellhaven Willow Creek",
        "Riverbend Manor Care Center": "Bellhaven of Chagrin Falls",
        "Bellhaven Health Care Center of Ashland": "Bellhaven Healthcare Centre of Ashland",
        "Bellhaven Rehab and Nursing of Grove City": "Bellhaven Rehabilitation & Nursing of Grove City",
        "Bellhaven of Sycamore Ridge": "Bellhaven at Sycamore Ridge",
        "Arbors at Bellhaven Dayton": "The Arbors at Bellhaven - Dayton",
    }
    for old, new in expected.items():
        assert renames.get(aid(accounts, old)) == new, old


def test_street_fix_only_when_substantive(props, accounts):
    """Toledo differs only in formatting (NW vs Northwest) -> no proposal.
    Ashtabula holds a PO box instead of an address -> proposal."""
    street_fixes = {p.target_account_id for p in of_kind(props, "FIELD_FIX")
                    if "billing_street" in p.changes}
    assert aid(accounts, "Bellhaven Woods of Toledo") not in street_fixes
    assert aid(accounts, "Bellhaven of Ashtabula") in street_fixes


def test_portsmouth_gets_zip_fix(props, accounts):
    """CRM 45626 vs website 45662 — transposed digits."""
    target = aid(accounts, "Bellhaven of Portsmouth")
    p = next(p for p in of_kind(props, "FIELD_FIX") if p.target_account_id == target)
    assert p.changes["billing_zip"] == "45662"


def test_care_type_no_overwrite(props, accounts):
    """The two vocabularies don't line up; only ever fill a blank."""
    populated = {a["account_id"] for a in accounts if a["care_type"]}
    for p in of_kind(props, "FIELD_FIX"):
        if "care_type" in p.changes:
            assert p.target_account_id not in populated


def test_four_greenfield_creates(props):
    created = {p.site_slug for p in of_kind(props, "CREATE")}
    assert created == {"amberly-manor", "bellhaven-at-union-square",
                       "bellhaven-of-batavia", "bellhaven-of-carlisle"}
    for p in of_kind(props, "CREATE"):
        assert p.new_account["parent_id"] == BH
        assert p.new_account["status"] == "Active"
        assert p.target_account_id == ""


# --- delisted ------------------------------------------------------------

def test_sandusky_inactive_with_evidence(props, accounts):
    target = aid(accounts, "Bellhaven of Sandusky")
    p = next(p for p in of_kind(props, "DELISTED") if p.target_account_id == target)
    assert p.changes["status"] == "Inactive"
    assert "Millstone" in p.changes["note"]
    assert aid(accounts, "Millstone Care of Sandusky") in p.changes["note"]


def test_alliance_and_coldwater_needs_review_not_inactive(props, accounts):
    """No corroborating evidence of a sale. A website can simply be stale, and
    deactivating a live account is worse than flagging it."""
    for name in ("Bellhaven Care Center of Alliance", "Bellhaven of Coldwater"):
        target = aid(accounts, name)
        p = next(p for p in of_kind(props, "DELISTED") if p.target_account_id == target)
        assert p.changes["status"] == "Needs Review", name


def test_delisted_covers_exactly_three(props):
    assert len(of_kind(props, "DELISTED")) == 3


# --- scope ---------------------------------------------------------------

def test_nothing_outside_the_bellhaven_lane_is_touched(props, accounts, sites):
    """The website is the only source of truth we have, and it only speaks to
    Bellhaven. An account may be touched only if it matched a listed community
    or already sits under the Bellhaven parent."""
    by_id = {a["account_id"]: a for a in accounts}
    matched = {m.account_id for m in match_all(sites, accounts)}
    for p in props:
        if not p.target_account_id:
            continue
        acct = by_id[p.target_account_id]
        assert p.target_account_id in matched or acct["parent_id"] == BH, \
            f"out-of-scope: {acct['name']} ({acct.get('parent_name')})"


def test_every_proposal_carries_evidence(props):
    for p in props:
        assert p.evidence, f"{p.kind} has no evidence"
        assert p.changes or p.new_account, f"{p.kind} proposes nothing"
        assert 0 < p.confidence <= 100


# --- re-run stability ----------------------------------------------------

def test_resolved_duplicates_leave_the_candidate_pool(sites, accounts):
    """Once a losing copy carries duplicate_of_account it is retired. It must
    not be re-considered, or a later parent change could elect a different
    survivor and propose a contradictory link."""
    retired_name = "Kettering Care Centre"
    survivor_id = aid(accounts, "Kettering Senior Campus")
    after = [
        {**a, "duplicate_of_account": survivor_id, "status": "Inactive"}
        if a["name"] == retired_name else a
        for a in accounts
    ]
    props = build_proposals(sites, after, match_all(sites, after))
    retired_id = aid(accounts, retired_name)
    assert not [p for p in props if p.target_account_id == retired_id], \
        "retired duplicate was re-proposed"


def test_retired_duplicate_is_not_flagged_delisted(sites, accounts):
    """A retired copy that sits under the Bellhaven parent must still count as
    claimed, or it reappears as a DELISTED 'Needs Review'."""
    owosso = [a for a in accounts if a["name"] == "Bellhaven of Owosso"]
    assert len(owosso) == 2
    retired_id, survivor_id = sorted(a["account_id"] for a in owosso)
    after = [
        {**a, "duplicate_of_account": retired_id, "status": "Inactive"}
        if a["account_id"] == survivor_id else a
        for a in accounts
    ]
    props = build_proposals(sites, after, match_all(sites, after))
    delisted = {p.target_account_id for p in props if p.kind == "DELISTED"}
    assert survivor_id not in delisted
