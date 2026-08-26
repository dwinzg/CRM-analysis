# Bellhaven CRM Ownership Reconciliation

Bellhaven Senior Living lists all of its communities on its
[website](https://analyst-assessment-production.up.railway.app). The CRM also
tracks who owns what, using a `parent_id` on each account. Those two pictures
drift apart as facilities get bought, rebranded and consolidated.

This tool compares them, works out what changed, and proposes a fix for each
difference along with the evidence behind it. A human approves or rejects each
one, and only the approved changes get written back through the API.

Nothing reaches the CRM without someone pressing Approve.

## Setup

```bash
python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'
cp .env.example .env          # then paste your CRM token into it
```

## Running it

```bash
make test          # 110 tests, all offline. No network and no token needed.
make run           # scrape the site, match against the CRM, queue proposals
make review        # http://localhost:8000 to approve or reject, with evidence
make apply         # write only the approved proposals back to the CRM
```

You can run `make run` as often as you like. Anything you already approved or
rejected will not come back. If you want to try the pipeline without touching
the network, `make run-offline` replays the saved fixtures in
`tests/fixtures/`.

To write approved changes you can either press **Apply approved to CRM** in the
review app or run `make apply`. Both do the same thing.

## How the pieces fit together

```
scrape.py ──► normalize.py ──► match.py ──► classify.py ──► ledger.py (sqlite)
                                                                 │
                                              review/app.py ◄────┤  approve / reject
                                                                 ▼
                                                             apply.py ──► CRM API
```

| File | What it does |
|---|---|
| `bellhaven/config.py` | URLs, the Bellhaven parent id, care-type mapping, match thresholds. No logic. |
| `bellhaven/crm.py` | `CrmClient`. The only place that talks to the CRM API. |
| `bellhaven/scrape.py` | Turns the website into `SiteLocation` records. The only place that talks to the website. |
| `bellhaven/normalize.py` | String cleanup for addresses, names, phones and zips. |
| `bellhaven/match.py` | Decides which CRM account goes with which community. |
| `bellhaven/classify.py` | Turns a match into a proposed change. Holds the CHOW billing rule. |
| `bellhaven/ledger.py` | SQLite storage plus fingerprinting, which is what makes re-runs safe. |
| `bellhaven/pipeline.py` | Ties it together. Proposes only, never writes. |
| `bellhaven/apply.py` | Writes approved proposals to the CRM. |
| `review/app.py` | The review queue. The only way something becomes approved. |
| `.github/workflows/daily.yml` | Daily schedule. Proposes only, never applies. |
| `ops/crontab.example` | Same schedule, for cron instead. |

Apart from `crm.py`, `scrape.py` and `apply.py`, none of it touches the network,
which is why the tests run offline against saved fixtures.

## Configuration

| Variable | Where | What it is for |
|---|---|---|
| `CRM_TOKEN` | `.env`, which is gitignored | CRM API token. Needed for `make run` and `make apply`. |
| `BELLHAVEN_DB` | environment, optional | Where the ledger lives. Defaults to `data/ledger.db`. |

The match thresholds (`NAME_CONFIDENT` and `NAME_REVIEW`) and the mapping from
website care offerings to CRM care types (`CARE_MAP`) are both in
`bellhaven/config.py`.

## Scheduling

`.github/workflows/daily.yml` holds the schedule we would use, which is 09:00
ET each day. **The cron trigger is commented out**, so right now the workflow
only runs when you trigger it by hand. Uncomment the two `schedule` lines and
set `CRM_TOKEN` as a repository secret to turn it on.

When it runs it tests, restores the proposal ledger from cache, proposes, and
uploads the ledger as an artifact. It never applies anything, so an unattended
run cannot change the CRM.

`ops/crontab.example` is the same schedule for cron, if you would rather run it
locally. It is an example file and is not installed for you.
