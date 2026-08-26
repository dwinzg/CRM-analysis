"""Matcher tests. Frozen fixtures only; no network.

The interesting assertions are the negative ones: this dataset is seeded with
name-similarity decoys, and a matcher that trusts names will confidently link
facilities that are hundreds of miles apart.
"""
import json

import pytest

from bellhaven.match import match_all, passes_gate, score_pair
from bellhaven.scrape import SiteLocation


@pytest.fixture(scope="module")
def sites():
    return [SiteLocation(**d) for d in json.load(open("tests/fixtures/site.json"))]


@pytest.fixture(scope="module")
def accounts():
    return json.load(open("tests/fixtures/accounts.json"))


def by_slug(sites, slug):
    return next(s for s in sites if s.slug == slug)


def by_name(accounts, name):
    return next(a for a in accounts if a["name"] == name)


def by_id(accounts, aid):
    return next(a for a in accounts if a["account_id"] == aid)


def fake_site_from(acct, **over):
    """Build a SiteLocation that mirrors a CRM account, for gate probing."""
    base = dict(slug="probe", name=acct["name"], street=acct["billing_street"],
                city=acct["billing_city"], state=acct["billing_state"],
                zip=acct["billing_zip"], care=[], phone=acct.get("phone", ""), url="")
    base.update(over)
    return SiteLocation(**base)


# --- the decoys ----------------------------------------------------------

def test_gate_rejects_cross_city_namesake(sites, accounts):
    """Amberly Manor (Hudson, OH) vs Amberly Care Center (Grand Rapids, MI).
    ~88% name similarity, different state. THE decoy in this dataset."""
    site = by_slug(sites, "amberly-manor")
    acct = by_name(accounts, "Amberly Care Center")
    assert not passes_gate(site, acct)
    assert score_pair(site, acct) is None


def test_amberly_has_no_match_at_all(sites, accounts):
    site = by_slug(sites, "amberly-manor")
    assert match_all([site], accounts) == []


#: Name-similar account clusters seeded through the CRM. Similarity runs 72-76
#: across every pair, which is exactly high enough to fool a name-only matcher.
DECOY_CLUSTERS = [
    ["Willowbrook Assisted Living", "Willowbrook Gardens",
     "Willowbrook Nursing & Rehabilitation"],
    ["Rosewood Manor", "Rosewood Village"],
    ["Golden Gate Gardens", "Golden Gate Health Campus"],
    ["Winding Creek Estates", "Winding Creek Health Campus"],
    ["Heritage Estates", "Heritage Place"],
    ["Aspen Court Assisted Living", "Aspen Court Nursing & Rehabilitation"],
]


@pytest.mark.parametrize("cluster", DECOY_CLUSTERS)
def test_decoy_clusters_never_reach_a_confident_tier(accounts, cluster):
    """The gate stops cross-city namesakes outright. Same-city namesakes
    (Winding Creek, both in Scranton PA) survive the gate by design — the gate
    is a candidate filter, not a verdict — but must never reach a confident
    tier on name similarity alone. Tier D is the correct landing spot: flagged,
    not asserted."""
    accts = [by_name(accounts, n) for n in cluster]
    for a in accts:
        for b in accts:
            if a["account_id"] == b["account_id"]:
                continue
            m = score_pair(fake_site_from(a), b)
            assert m is None or m.tier in ("D", "E"), \
                f"{a['name']} confidently matched {b['name']} at tier {m.tier}"


@pytest.mark.parametrize("cluster", DECOY_CLUSTERS)
def test_cross_city_decoys_rejected_by_the_gate(accounts, cluster):
    """Anything in a different city AND different zip must not even be a
    candidate, however similar the names look."""
    accts = [by_name(accounts, n) for n in cluster]
    for a in accts:
        for b in accts:
            if a["account_id"] == b["account_id"]:
                continue
            different_place = (a["billing_city"] != b["billing_city"]
                               and a["billing_zip"] != b["billing_zip"])
            if different_place:
                assert not passes_gate(fake_site_from(a), b), \
                    f"{a['name']} matched {b['name']} across cities"


# --- the tiers -----------------------------------------------------------

def test_tier_a_exact_street_and_zip(sites, accounts):
    m = score_pair(by_slug(sites, "bellhaven-woods-of-toledo"),
                   by_name(accounts, "Bellhaven Woods of Toledo"))
    assert m.tier == "A" and m.confidence == 100
    assert "street+zip" in m.signals


def test_tier_b_survives_transposed_zip(sites, accounts):
    """Portsmouth: CRM zip 45626, website 45662. Street is identical."""
    m = score_pair(by_slug(sites, "bellhaven-of-portsmouth"),
                   by_name(accounts, "Bellhaven of Portsmouth"))
    assert m.tier == "B"
    assert "street+city+state" in m.signals


def test_tier_c_rescues_po_box(sites, accounts):
    """Ashtabula's CRM street is 'PO Box 517' — address tiers can't apply."""
    m = score_pair(by_slug(sites, "bellhaven-of-ashtabula"),
                   by_name(accounts, "Bellhaven of Ashtabula"))
    assert m.tier == "C"


def test_rebrand_matched_by_address_not_name(sites, accounts):
    """Sunny Acres Retirement Home -> Bellhaven Willow Creek. Names share
    nothing; the address carries the match."""
    m = score_pair(by_slug(sites, "bellhaven-willow-creek"),
                   by_name(accounts, "Sunny Acres Retirement Home"))
    assert m is not None and m.tier == "A"


# --- cardinality ---------------------------------------------------------

def test_match_returns_all_candidates_not_just_first(sites, accounts):
    """Kettering has THREE CRM accounts at 3313 Wilmington Pike, under three
    different parents. A first-hit join hides the most important finding."""
    ms = match_all([by_slug(sites, "bellhaven-of-kettering")], accounts)
    assert len(ms) == 3
    assert {by_id(accounts, m.account_id)["name"] for m in ms} == {
        "Kettering Care Centre", "Kettering Senior Campus",
        "Kettering Nursing & Rehabilitation"}


def test_monroe_trio_all_found(sites, accounts):
    ms = match_all([by_slug(sites, "bellhaven-gardens-of-monroe")], accounts)
    assert {by_id(accounts, m.account_id)["name"] for m in ms} == {
        "Bellhaven Gardens of Monroe", "Cedar Trail of Monroe",
        "Monroe Gardens Care Center"}


def test_one_account_never_claimed_by_two_locations(sites, accounts):
    """A CRM account belonging to two website communities would mean the
    pipeline proposes contradictory changes to the same record."""
    ms = match_all(sites, accounts)
    seen = {}
    for m in ms:
        seen.setdefault(m.account_id, set()).add(m.site_slug)
    clashes = {a: s for a, s in seen.items() if len(s) > 1}
    assert not clashes, f"accounts claimed by multiple locations: {clashes}"


def test_every_site_location_resolves(sites, accounts):
    """Coverage guard. Only the four genuinely-absent facilities may go
    unmatched; anything else means a matcher regression."""
    matched = {m.site_slug for m in match_all(sites, accounts)}
    unmatched = {s.slug for s in sites} - matched
    assert unmatched == {"amberly-manor", "bellhaven-at-union-square",
                         "bellhaven-of-batavia", "bellhaven-of-carlisle"}
