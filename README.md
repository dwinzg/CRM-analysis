# Bellhaven CRM Ownership Reconciliation

Reconciles [Bellhaven Senior Living's](https://analyst-assessment-production.up.railway.app)
public community list against the CRM sandbox, proposes evidence-backed corrections,
gates every one of them behind human approval, and writes the approved ones back.

> Full writeup — matching logic, the CHOW SOP, judgment calls, and what I deliberately
> left alone — is below the Quickstart.

## Quickstart

```bash
python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'
cp .env.example .env          # then paste your CRM token in
make test                     # offline; no network
make run                      # scrape + match + queue proposals
make review                   # http://localhost:8000 — approve/reject
make apply                    # writes ONLY approved proposals
```

## How it works

```
scrape.py ──► normalize.py ──► match.py ──► classify.py ──► ledger.py (sqlite)
                                                                 │
                                              review/app.py ◄────┤  approve / reject
                                                                 ▼
                                                             apply.py ──► CRM API
```

| File | Responsibility |
|---|---|
| `bellhaven/config.py` | Constants only: URLs, parent ids, care-type map, thresholds. No logic. |
| `bellhaven/crm.py` | `CrmClient` — the only module that talks to the CRM API. |
| `bellhaven/scrape.py` | Website → `SiteLocation`. The only module that talks to the website. |
| `bellhaven/normalize.py` | Pure string functions for addresses, names, phones, zips. Imports nothing from the package. |
| `bellhaven/match.py` | Candidate gate + ordered tiers → `MatchResult`. Pure. |
| `bellhaven/classify.py` | Matches → `Proposal`s. Holds the CHOW SOP and the duplicate rules. Pure. |
| `bellhaven/ledger.py` | SQLite persistence + fingerprinting. Makes re-runs safe. No business logic. |
| `bellhaven/pipeline.py` | Orchestration: scrape, fetch, match, classify, upsert. |
| `bellhaven/apply.py` | Approved proposals → CRM writes. Pre-flight verified. |
| `review/app.py` | FastAPI review queue. The only way a proposal becomes approved. |
| `tests/` | Offline. Every assertion is a real row from this dataset. |
| `.github/workflows/daily.yml` | Daily schedule. **Proposes only — never applies.** |

Everything except `crm.py`, `scrape.py`, and `apply.py` is network-free and unit-tested
against frozen fixtures in `tests/fixtures/`.

## Matching logic

<!-- filled in at Task 12 -->

## Classification and the CHOW SOP

<!-- filled in at Task 12 -->

## Judgment calls

<!-- filled in at Task 12 -->

## Re-run safety

<!-- filled in at Task 12 -->

## Schedule

<!-- filled in at Task 12 -->

## What I deliberately did not touch

<!-- filled in at Task 12 -->

## Time spent

<!-- filled in at Task 12 -->
