"""Turns matches into proposed changes. The billing SOP lives here.

Order matters. Duplicates resolve first, so exactly one account per facility
receives the re-parent, rename and field fixes. The retired copies get nothing
but their duplicate marker.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

from . import config
from .normalize import is_po_box, norm_city, norm_street, norm_zip

BH = config.BELLHAVEN_PARENT_ID


@dataclass
class Proposal:
    kind: str
    target_account_id: str
    site_slug: str
    changes: dict
    evidence: dict
    confidence: int
    new_account: dict | None = None


def needs_chow(acct: dict) -> bool:
    """The billing SOP, in one line.

    Revenue history AND outstanding AR both greater than zero means billing
    needs the old record frozen: create a successor under the correct parent
    and link the old one to it, rather than re-parenting in place.

    The AND is load-bearing. Lima (rev 47,000 / AR 0) and Findlay
    (rev 22,000 / AR 0) both carry real revenue and are ordinary re-parents.
    """
    return (acct.get("lifetime_revenue") or 0) > 0 and (acct.get("outstanding_ar") or 0) > 0


def pick_survivor(accts: list[dict]) -> dict:
    """Which copy of a duplicated facility to keep, in strict precedence order:

    1. Already under Bellhaven. The record the sales team is currently using.
    2. Most revenue history, then most outstanding AR. A billing signal beats
       everything below it, because losing a billed record has real cost.
    3. Has any parent at all. Between two otherwise identical records, the one
       with an acquisition lineage carries more provenance than an orphan.
    4. Lowest account id, so the choice is stable across runs.

    Contact counts were evaluated as a tiebreak and rejected: in this dataset
    they are zero across every ambiguous cluster, so they would add a network
    dependency to a pure module and change no decision.
    """
    return sorted(accts, key=lambda a: (
        a.get("parent_id") != BH,
        -(a.get("lifetime_revenue") or 0),
        -(a.get("outstanding_ar") or 0),
        not a.get("parent_id"),
        a["account_id"],
    ))[0]


def _site_care_type(site) -> str:
    """Collapse the website's care badges to one CRM care_type value."""
    for badge in site.care:
        if badge in config.CARE_MAP:
            return config.CARE_MAP[badge]
    return ""


def _new_account_payload(site, note: str) -> dict:
    return {
        "name": site.name,
        "parent_id": BH,
        "billing_street": site.street,
        "billing_city": site.city,
        "billing_state": site.state,
        "billing_zip": site.zip,
        "care_type": _site_care_type(site),
        "phone": site.phone,
        "status": "Active",
        "note": note,
    }


def _before(acct: dict | None, changes: dict) -> dict:
    """The current value of every field a proposal would change.

    The review UI shows this as the Current column. Without it a reviewer is
    asked to approve an overwrite without being shown what is overwritten,
    and apply.py has nothing to compare against to detect that the CRM moved.
    """
    if not acct:
        return {}
    return {k: acct.get(k, "") for k in changes if k != "note"}


def _evidence(site, acct: dict | None, match) -> dict:
    ev: dict = {"tier": getattr(match, "tier", None),
                "signals": list(getattr(match, "signals", []))}
    if site is not None:
        ev["site"] = {
            "name": site.name,
            "address": f"{site.street}, {site.city}, {site.state} {site.zip}",
            "care": list(site.care),
            "phone": site.phone,
            "url": site.url,
        }
    if acct:
        ev["crm"] = {
            "account_id": acct.get("account_id"),
            "name": acct.get("name"),
            "address": f"{acct.get('billing_street')}, {acct.get('billing_city')}, "
                       f"{acct.get('billing_state')} {acct.get('billing_zip')}",
            "parent": acct.get("parent_name") or "(no parent)",
            "status": acct.get("status"),
            "care_type": acct.get("care_type"),
            "lifetime_revenue": acct.get("lifetime_revenue"),
            "outstanding_ar": acct.get("outstanding_ar"),
        }
    return ev


def build_proposals(sites, accounts: list[dict], matches) -> list[Proposal]:
    by_id = {a["account_id"]: a for a in accounts}
    matches_by_site = defaultdict(list)
    for m in matches:
        matches_by_site[m.site_slug].append(m)

    proposals: list[Proposal] = []
    claimed: set[str] = set()

    for site in sites:
        site_matches = matches_by_site.get(site.slug, [])

        # No CRM account for this community at all.
        if not site_matches:
            proposals.append(Proposal(
                kind="CREATE",
                target_account_id="",
                site_slug=site.slug,
                changes={},
                evidence=_evidence(site, None, None),
                confidence=100,
                new_account=_new_account_payload(
                    site, f"Created from {site.url}. No existing CRM account matched "
                          f"this address or name within {site.city}, {site.state}."),
            ))
            continue

        match_by_id = {m.account_id: m for m in site_matches}
        matched_accounts = [by_id[m.account_id] for m in site_matches]

        # Every matched account counts as claimed, including ones already
        # retired, so a resolved duplicate is never mistaken for a delisted
        # facility on a later run.
        claimed.update(a["account_id"] for a in matched_accounts)

        # ...but a record already marked as a duplicate of another is retired,
        # not a candidate. Leaving it in the pool means a later parent change
        # could elect a new survivor and mint a contradictory duplicate link.
        candidates = [a for a in matched_accounts if not a.get("duplicate_of_account")]
        if not candidates:
            continue

        survivor = pick_survivor(candidates)
        survivor_chow = survivor.get("parent_id") != BH and needs_chow(survivor)

        # 1. Retire the losing copies. They receive nothing else.
        for acct in candidates:
            if acct["account_id"] == survivor["account_id"]:
                continue
            note = (f"Duplicate of {survivor['name']} ({survivor['account_id']}). "
                    f"Same facility at {site.street}, {site.city}, {site.state} "
                    f"{site.zip}. Source: {site.url}")
            if survivor_chow:
                # The survivor is about to be frozen under its old parent, so
                # say where the live record actually is. Without this the trail
                # from a retired copy to the successor is invisible.
                note += (f" Note that {survivor['name']} is itself preserved under its "
                         f"existing parent per the billing SOP; follow its "
                         f"chow_current_account field to the live Bellhaven account.")
            proposals.append(Proposal(
                kind="DUPLICATE",
                target_account_id=acct["account_id"],
                site_slug=site.slug,
                changes={
                    "duplicate_of_account": survivor["account_id"],
                    "status": "Inactive",
                    "note": note,
                },
                evidence=_evidence(site, acct, match_by_id[acct["account_id"]]),
                confidence=match_by_id[acct["account_id"]].confidence,
            ))

        acct = survivor
        match = match_by_id[survivor["account_id"]]
        wrong_parent = acct.get("parent_id") != BH
        chow = survivor_chow

        # 2. Ownership.
        if chow:
            proposals.append(Proposal(
                kind="CHOW",
                target_account_id=acct["account_id"],
                site_slug=site.slug,
                changes={
                    # No parent_id. That is the entire point of the SOP.
                    "chow_current_account": "<successor>",
                    "note": f"Change of ownership: facility is now operated by Bellhaven "
                            f"Senior Living (source: {site.url}). Per billing SOP this "
                            f"account is preserved unchanged because it has revenue history "
                            f"({acct['lifetime_revenue']}) AND outstanding AR "
                            f"({acct['outstanding_ar']}). A successor account has been "
                            f"created under Bellhaven and linked here.",
                },
                evidence=_evidence(site, acct, match),
                confidence=match.confidence,
                new_account=_new_account_payload(
                    site, f"CHOW successor to {acct['name']} ({acct['account_id']}), which "
                          f"is preserved for billing. Source: {site.url}"),
            ))
            # 'Leave the existing account exactly as it is', so nothing further.
            continue

        if wrong_parent:
            proposals.append(Proposal(
                kind="REPARENT",
                target_account_id=acct["account_id"],
                site_slug=site.slug,
                changes={
                    "parent_id": BH,
                    "note": f"Re-parented to Bellhaven Senior Living; listed at {site.url}. "
                            f"Re-parented in place rather than CHOW because outstanding AR "
                            f"is {acct.get('outstanding_ar') or 0} "
                            f"(lifetime revenue {acct.get('lifetime_revenue') or 0}).",
                },
                evidence=_evidence(site, acct, match),
                confidence=match.confidence,
            ))

        # 3. Name.
        if (acct.get("name") or "") != site.name:
            proposals.append(Proposal(
                kind="RENAME",
                target_account_id=acct["account_id"],
                site_slug=site.slug,
                changes={
                    "name": site.name,
                    "note": f"Renamed to match the public listing at {site.url} "
                            f"(was '{acct.get('name')}').",
                },
                evidence=_evidence(site, acct, match),
                confidence=match.confidence,
            ))

        # 4. Address and care-type drift.
        fixes: dict = {}
        # Only substantive street changes. Pure formatting drift ("NW" vs
        # "Northwest") would bury the real findings in noise.
        if is_po_box(acct.get("billing_street")) or \
                norm_street(acct.get("billing_street")) != norm_street(site.street):
            fixes["billing_street"] = site.street
        if norm_zip(acct.get("billing_zip")) != norm_zip(site.zip):
            fixes["billing_zip"] = site.zip
        if norm_city(acct.get("billing_city")) != norm_city(site.city):
            fixes["billing_city"] = site.city
        # Fill a blank care_type, never overwrite: the vocabularies don't line
        # up and the site may list two offerings where the CRM holds one.
        if not acct.get("care_type") and _site_care_type(site):
            fixes["care_type"] = _site_care_type(site)
        if fixes:
            changed = ", ".join(sorted(fixes))
            fixes["note"] = f"Synced {changed} from the public listing at {site.url}."
            proposals.append(Proposal(
                kind="FIELD_FIX",
                target_account_id=acct["account_id"],
                site_slug=site.slug,
                changes=fixes,
                evidence=_evidence(site, acct, match),
                confidence=match.confidence,
            ))

    proposals.extend(_delisted(accounts, claimed))

    # Record what each change replaces. Done centrally so no construction site
    # can forget it.
    for p in proposals:
        p.evidence["before"] = _before(by_id.get(p.target_account_id), p.changes)
    return proposals


def _delisted(accounts: list[dict], claimed: set[str]) -> list[Proposal]:
    """Accounts under Bellhaven that no website community matched.

    Absence from a website is not proof of a sale. A site can simply be stale,
    and wrongly deactivating a live account is worse for a sales team than
    leaving it flagged. So Inactive requires positive corroboration: another
    operator's account at the identical address. Everything else is flagged for
    a human.
    """
    by_address = defaultdict(list)
    for a in accounts:
        street = norm_street(a.get("billing_street"))
        zipcode = norm_zip(a.get("billing_zip"))
        # An empty address is not an address. Without this guard every account
        # missing a street and zip lands in the same ('', '') bucket and
        # "corroborates" a divestiture for every other one, which is the only
        # place this tool asserts a deactivation rather than flagging it.
        if not street or not zipcode or is_po_box(a.get("billing_street")):
            continue
        by_address[(street, zipcode)].append(a)

    out: list[Proposal] = []
    for acct in accounts:
        if acct.get("parent_id") != BH or acct["account_id"] in claimed:
            continue

        key = (norm_street(acct.get("billing_street")), norm_zip(acct.get("billing_zip")))
        rivals = [o for o in by_address.get(key, [])
                  if o["account_id"] != acct["account_id"]
                  # `not o.get("parent_id")` rather than `== ""`: the API
                  # declares no response schema, so an orphan may arrive as
                  # JSON null, and an orphan is not another operator.
                  and o.get("parent_id") and o.get("parent_id") != BH]

        if rivals:
            rival = rivals[0]
            out.append(Proposal(
                kind="DELISTED",
                target_account_id=acct["account_id"],
                site_slug="",
                changes={
                    "status": "Inactive",
                    "note": f"No longer listed on the Bellhaven website. The identical "
                            f"address is held by {rival['name']} ({rival['account_id']}) "
                            f"under {rival.get('parent_name')}, which corroborates a "
                            f"divestiture. Account preserved for billing "
                            f"(lifetime revenue {acct.get('lifetime_revenue') or 0}, "
                            f"outstanding AR {acct.get('outstanding_ar') or 0}).",
                },
                evidence={**_evidence(None, acct, None),
                          "signals": ["absent-from-website", "address-held-by-other-parent"],
                          "rival": {"name": rival["name"], "account_id": rival["account_id"],
                                    "parent": rival.get("parent_name")}},
                confidence=90,
            ))
        else:
            out.append(Proposal(
                kind="DELISTED",
                target_account_id=acct["account_id"],
                site_slug="",
                changes={
                    "status": "Needs Review",
                    "note": "Not listed on the Bellhaven website. No corroborating CRM "
                            "evidence of a sale, so ownership is flagged rather than "
                            "asserted. Confirm with corporate.",
                },
                evidence={**_evidence(None, acct, None),
                          "signals": ["absent-from-website", "no-corroboration"]},
                confidence=60,
            ))
    return out
