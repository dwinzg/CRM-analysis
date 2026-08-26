"""Links website locations to CRM accounts.

Two-stage by design:

1. A hard GATE decides whether a pair is even a candidate. State must match,
   plus at least one of city / zip / phone. This exists because the dataset is
   seeded with name-similarity decoys — Amberly Manor (Hudson, OH) scores ~88%
   against Amberly Care Center (Grand Rapids, MI), and the Willowbrook,
   Rosewood, Golden Gate and Winding Creek clusters are the same trap. Name
   similarity on its own is actively dangerous here.

2. Ordered TIERS, first hit wins. Tiers rather than a weighted score because a
   reviewer has to understand why a match happened, and because adjusting a
   named rule is safer and more reviewable than nudging a coefficient.

Every tier records the signals that fired. That list is the evidence the review
app renders — there is no separate evidence-building step.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from . import config
from .normalize import (is_po_box, name_sim, norm_city, norm_phone, norm_street,
                        norm_zip)


@dataclass
class MatchResult:
    site_slug: str
    account_id: str
    tier: str
    confidence: int
    signals: list[str] = field(default_factory=list)


def passes_gate(site, acct: dict) -> bool:
    """State must match, AND one of city / zip / phone.

    This is what stops a namesake in another state from ever being considered.
    """
    if (site.state or "").upper() != (acct.get("billing_state") or "").upper():
        return False
    if norm_city(site.city) and norm_city(site.city) == norm_city(acct.get("billing_city")):
        return True
    site_zip = norm_zip(site.zip)
    if site_zip and site_zip == norm_zip(acct.get("billing_zip")):
        return True
    phone = norm_phone(site.phone)
    return bool(phone) and phone == norm_phone(acct.get("phone"))


def score_pair(site, acct: dict) -> MatchResult | None:
    """Best tier for one (location, account) pair, or None."""
    if not passes_gate(site, acct):
        return None

    site_street = norm_street(site.street)
    crm_street = norm_street(acct.get("billing_street"))
    # A PO box is a mailing address; comparing it to a street is meaningless.
    address_usable = bool(site_street) and bool(crm_street) and not is_po_box(acct.get("billing_street"))
    same_city = norm_city(site.city) == norm_city(acct.get("billing_city"))
    same_street = address_usable and site_street == crm_street
    similarity = name_sim(site.name, acct.get("name"))

    def result(tier, confidence, signals):
        return MatchResult(site.slug, acct["account_id"], tier, confidence, signals)

    if same_street and norm_zip(site.zip) == norm_zip(acct.get("billing_zip")):
        return result("A", 100, ["street+zip"])
    if same_street and same_city:
        return result("B", 95, ["street+city+state", "zip-mismatch"])
    if similarity >= config.NAME_CONFIDENT and same_city:
        signals = [f"name~{int(similarity)}", "city+state"]
        if is_po_box(acct.get("billing_street")):
            signals.append("crm-street-is-po-box")
        return result("C", int(similarity), signals)
    if similarity >= config.NAME_REVIEW and same_city:
        return result("D", int(similarity), [f"name~{int(similarity)}", "city+state", "needs-review"])
    phone = norm_phone(site.phone)
    if phone and phone == norm_phone(acct.get("phone")):
        return result("E", 60, ["phone+state", "needs-review"])
    return None


def match_all(sites, accounts: list[dict]) -> list[MatchResult]:
    """Every (location, account) match. Deliberately many-to-many.

    Returning all candidates rather than the best one is what surfaces the
    duplicate clusters — Kettering has three accounts at one address, and a
    first-hit join would report a clean match and hide the other two.
    """
    out: list[MatchResult] = []
    for site in sites:
        for acct in accounts:
            m = score_pair(site, acct)
            if m is not None:
                out.append(m)
    return out
