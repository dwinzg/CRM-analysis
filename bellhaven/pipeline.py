"""Orchestration: scrape, fetch, match, classify, record.

Proposes only. Nothing here writes to the CRM. That is apply.py, and it runs
only against proposals a human approved in the review app.
"""
from __future__ import annotations

import argparse
import collections
import json

from . import config
from .classify import build_proposals
from .crm import CrmClient
from .ledger import Ledger
from .match import match_all
from .scrape import SiteLocation, scrape_all


def load_offline():
    """Frozen fixtures, for demos and for reasoning about the pipeline without
    hitting the network."""
    sites = [SiteLocation(**d) for d in json.load(open("tests/fixtures/site.json"))]
    accounts = json.load(open("tests/fixtures/accounts.json"))
    return sites, accounts


#: Offline runs get their own ledger. Pointing them at the live one would
#: replace the real proposal queue with the fixture queue and mark every
#: genuine pending proposal stale, which is a bad outcome for a command the
#: README recommends as the safe way to try things out.
OFFLINE_DB_PATH = "data/ledger-offline.db"


def ledger_path_for(offline: bool, db_path: str | None) -> str:
    if db_path:
        return db_path
    return OFFLINE_DB_PATH if offline else config.DB_PATH


def run(offline: bool = False, db_path: str | None = None) -> dict:
    sites, accounts = load_offline() if offline else (scrape_all(), CrmClient().list_accounts())
    matches = match_all(sites, accounts)
    proposals = build_proposals(sites, accounts, matches)

    ledger = Ledger(ledger_path_for(offline, db_path))
    stats = ledger.upsert(proposals)

    return {
        "source": "fixtures" if offline else "live",
        "ledger_path": ledger_path_for(offline, db_path),
        "locations_scraped": len(sites),
        "accounts_fetched": len(accounts),
        "matches": len(matches),
        "proposals": len(proposals),
        "by_kind": dict(sorted(collections.Counter(p.kind for p in proposals).items())),
        **stats,
        "ledger": ledger.counts(),
    }


def main() -> None:
    ap = argparse.ArgumentParser(description="Propose Bellhaven CRM corrections.")
    ap.add_argument("--offline", action="store_true",
                    help="use frozen fixtures instead of the live site and CRM")
    ap.add_argument("--db", default=None, help="ledger path (default: %s)" % config.DB_PATH)
    args = ap.parse_args()
    print(json.dumps(run(offline=args.offline, db_path=args.db), indent=2))


if __name__ == "__main__":
    main()
