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

Two stages, because the dataset punishes naive matching.

**Stage 1 — a hard gate.** A website location and a CRM account are only
*candidates* if the **state matches** and at least one of **city / zip / phone**
matches. Nothing else is considered.

That gate exists because of one record. `Amberly Manor` is a Bellhaven community
in Hudson, Ohio. The CRM contains `Amberly Care Center` in Grand Rapids,
Michigan. They score ~88% on name similarity and are different buildings 300
miles apart. The same trap is seeded four more times — Willowbrook (Flint MI /
Cleveland OH / Fort Wayne IN), Rosewood, Golden Gate and Winding Creek. Name
similarity alone is actively dangerous in this data, and `tests/test_match.py`
pins every one of those clusters.

**Stage 2 — ordered tiers, first hit wins.**

| Tier | Rule | Verdict |
|---|---|---|
| A | normalised street + zip | confident (100) |
| B | normalised street + city + state | confident (95), and propose the zip fix |
| C | name similarity ≥ 88 + city + state | confident |
| D | name similarity ≥ 70 + city + state | needs review |
| E | phone digits + state | needs review |
| — | no rule fires | no match |

Tiers rather than a weighted score, for two reasons: a reviewer has to
understand *why* a match happened, and retuning a named rule under observation
is safer than nudging a coefficient. Thresholds live in `bellhaven/config.py`.

Each tier records the signals that fired, and that list *is* the evidence the
review app renders — there is no separate evidence-assembly step that could
drift from the logic.

**Normalisation** (`bellhaven/normalize.py`) handles the drift actually present:

| CRM | Website | Handled by |
|---|---|---|
| `4850 Northwest Sylvania Avenue` | `4850 NW Sylvania Ave` | directional + suffix maps |
| `1250 Northwest Franklin St` | `1250 NW Franklin Street` | same |
| `2222 Gallia St`, zip `45626` | `2222 Gallia St`, zip `45662` | Tier B (transposed digits) |
| `PO Box 517` | `3156 W Prospect Rd` | PO-box detection → falls to Tier C |
| `Bellhaven Rehab and Nursing…` | `…Rehabilitation & Nursing…` | `&` ⇄ `and` |

`match_all` is deliberately many-to-many. A first-hit join would report a clean
match for Kettering and hide the fact that **three** CRM accounts share
3313 Wilmington Pike under three different parents.

**One coverage note.** The `/communities` directory paginates to 34 entries, but
`bellhaven-meadows-of-findlay` has a live detail page linked only from the
homepage. The scraper unions both sources and finds 35. A directory-only crawl
loses a real community silently, which is why `test_scrape.py` asserts the count.

## Classification and the CHOW SOP

Seven outcomes: `CREATE`, `REPARENT`, `CHOW`, `RENAME`, `FIELD_FIX`,
`DUPLICATE`, `DELISTED`.

Duplicates resolve **first**, so exactly one account per facility — the survivor
— receives the re-parent, rename and field fixes. Retired copies get nothing but
their duplicate marker.

### The SOP

> Revenue history **AND** outstanding AR above zero → preserve the old account
> untouched, create a successor under the correct parent, set
> `chow_current_account` on the old one. Otherwise re-parent in place.

The **AND** is the whole rule, and this dataset is built to catch an `or`:

| Account | Lifetime revenue | Outstanding AR | Action |
|---|---:|---:|---|
| Bellhaven of Marietta | 51,250 | **3,800** | **CHOW** |
| Bellhaven of Tiffin | 84,000 | **12,400** | **CHOW** |
| Bellhaven Crossings of Lima | 47,000 | **0** | re-parent in place |
| Bellhaven Meadows of Findlay | 22,000 | **0** | re-parent in place |

Lima and Findlay both carry real revenue. Treating them as CHOW would invent two
accounts and strand two billing records. `test_chow_truth_table` covers all four
rev/AR combinations.

A CHOW target receives `chow_current_account` and an explanatory note, and
nothing else — enforced by an early `continue` in `build_proposals`, not by
convention. `apply.py` rebuilds the patch from scratch with `parent_id` filtered
out, so the SOP holds even if a malformed proposal reaches the writer, and
`test_chow_creates_successor_and_never_patches_old_parent` asserts the key is
absent from the outgoing payload.

### Duplicates

The API has no merge and no delete, so a "merge" is: pick a survivor, set
`duplicate_of_account` on each loser to the survivor's id, mark it `Inactive`,
and explain in the note.

Survivor precedence, in strict order:

1. **Already under Bellhaven** — the record the sales team is using.
2. **Most revenue**, then **most outstanding AR** — a billing signal beats
   everything below it, because losing a billed record has real cost.
3. **Has any parent at all** — between otherwise identical records, an
   acquisition lineage beats an orphan.
4. **Lowest account id** — so the choice is stable across runs.

Contact counts were evaluated as a tiebreak and **rejected**: they are zero
across every ambiguous cluster here, so they would add a network dependency to a
pure module and change no decision.

Worked example — Kettering, three accounts at 3313 Wilmington Pike, all zero
revenue: `Kettering Senior Campus` (Cedar Trail) survives on the lineage rule,
gets re-parented and renamed to `Bellhaven of Kettering`; `Kettering Care Centre`
(Harborview) and the parentless `Kettering Nursing & Rehabilitation` are marked
duplicates of it.

### Care types

The two systems use different vocabularies — the site says
`Short-Term Rehabilitation & Nursing` and `Memory Support`, the CRM says
`Skilled Nursing` and `Memory Care` — and some communities list two offerings
where `care_type` holds one. `CARE_MAP` translates, but the mapping is lossy, so
it is only ever used to **fill a blank** `care_type`, never to overwrite one.
Diffing these fields naively would have produced ~30 junk proposals and buried
the real findings.

## Judgment calls

**Delisted accounts are evidence-tiered.** Absence from a website is not proof of
a sale; a site can simply be stale, and wrongly deactivating a live account hurts
a sales team more than leaving it flagged. So `Inactive` requires positive
corroboration:

- **Bellhaven of Sandusky** (revenue 130,000, AR 5,200) sits at 2715 Columbus
  Ave — the exact address of `Millstone Care of Sandusky` under Millstone Health
  Partners. That is real evidence of a divestiture → `Inactive`, with the
  Millstone account id in the note and the AR balance recorded, since billing
  still needs the record.
- **Alliance** and **Coldwater** have nothing corroborating → `Needs Review`,
  flagged for a human rather than asserted.

**Findlay is treated as current.** Its page is live and linked from the homepage;
it is simply missing from the directory listing. The alternative reading — that
it is being quietly delisted — is a stretch when the page still exists, so it is
re-parented to Bellhaven like any other listed community.

**Street formatting is not a change.** A proposal is only raised when the
*normalised* streets differ, or when the CRM holds a PO box. Toledo's
`Northwest` vs `NW` is cosmetic and raising it would add noise without adding
information.

**Notes explain every write.** Each proposal writes a `note` recording what
changed, why, and the source URL, so the reasoning survives in the CRM rather
than only in this repo.

## Re-run safety

Every proposal has a **fingerprint**:
`sha256(kind + target + every field that would change the CRM)`.

`note` is deliberately **excluded**. Notes carry dates ("not listed as of
2026-08-26"); hashing them would mint a fresh identity every day and a rejected
proposal would come back forever.

The fingerprint is the ledger's primary key and `upsert` uses
`INSERT OR IGNORE`, so a proposal already `approved` / `rejected` / `applied`
keeps its status and is never re-offered. Pending rows that stop appearing go
`stale`; decided rows are never touched, so vanishing from a run cannot
un-decide something.

`CREATE` records the new account id in `result_json`, and the next run matches
that account normally — no double-create. Writes are additionally guarded at
apply time: `apply.py` re-reads each target and marks the row `failed` rather
than overwriting a value that moved since the proposal was raised.

Verified end to end: a second consecutive run inserted **0**. After applying 26
proposals, the next run proposed only the 5 items still undecided.

## Schedule

`.github/workflows/daily.yml` — 09:00 ET daily, plus manual dispatch. It runs
the tests, restores the ledger from cache, proposes, writes a job summary, and
uploads the ledger as an artifact. **It never applies.** `ops/crontab.example`
is the local equivalent.

The split is deliberate: proposing is safe to automate, writing is not. Nothing
reaches the CRM without a human pressing Approve.

## Scope, and where this could go further

Not covered here — each is a deliberate boundary rather than an oversight, and
each is a reasonable next step:

- **Other operators' hierarchies.** The 26 parentless accounts and the Juniper
  Point / Stonebridge / Millstone trees are untouched. The Bellhaven website is
  the only source of truth available, and it only speaks to Bellhaven; proposing
  changes elsewhere would be unevidenced. `test_nothing_outside_the_bellhaven_lane_is_touched`
  enforces this.
- **The name-decoy clusters** (Willowbrook, Rosewood, Golden Gate, Winding
  Creek, Heritage, Aspen Court) are left alone on purpose. They look like
  duplicates and are not — different addresses in different states. Resolving
  them needs a source of truth we do not have.
- **Contacts.** The `/contacts` endpoint is read during analysis but never
  written. When accounts merge, their contacts arguably should too.
- **Address verification.** A USPS/geocoding lookup would replace the hand-built
  suffix tables and settle ambiguous cases such as `St` meaning Saint rather than
  Street.
- **Confidence calibration.** Tier thresholds are set from observed drift in this
  dataset. With decision history accumulating in the ledger, they could be tuned
  against actual approve/reject outcomes.
- **A real queue.** SQLite and a single-process app suit one reviewer. Several
  reviewers would need row locking and an audit trail of who decided what.
