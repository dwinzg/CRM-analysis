# Bellhaven CRM Ownership Reconciliation — Assessment Brief

> Reproduced for reference. **The API token has been redacted** — it lives in `.env`,
> which is gitignored. See `.env.example` for the variable name.

## Why this exercise

One of the toughest parts of keeping our CRM clean is that long term care facilities
are constantly being bought and sold. Roughly sixty percent of the LTC facilities we
work with are owned by a larger parent company, and ownership changes hands all the
time. Facilities get acquired, rebranded, split up, and consolidated, and every one of
those events can quietly break the link between a facility and its parent company in
our CRM.

Those links matter a lot. Selling to a corporate owned facility works differently than
selling to an independent one. Contracts often need corporate sign off, and outreach
frequently has to go through the corporate office rather than the facility itself. So
knowing which parent company owns a given facility at any given moment is core to how
our sales team operates, and keeping that ownership picture accurate is an ongoing,
real problem for our team.

This exercise is a scaled down version of a system we actually run in production. We
built a fictional operator so you can work with realistic, messy data without touching
our real CRM.

## The scenario

Bellhaven Senior Living is a senior care operator (fictional, built for this exercise).
Their communities are listed on their website. You have API access to our CRM sandbox,
which contains around 120 accounts of varying quality. Like any real CRM, some of the
data is stale, duplicated, or mislabeled.

| Resource | URL |
|---|---|
| Website | https://analyst-assessment-production.up.railway.app |
| CRM API base | https://analyst-assessment-production.up.railway.app/api/v1 |
| Interactive API docs | https://analyst-assessment-production.up.railway.app/api/docs |
| CRM browser (read only) | Click around the CRM the way a sales rep would. All changes still go through the API. |

Your personal API token (send it on every request):

```
bh_***REDACTED — see .env***
```

```bash
curl -H "Authorization: Bearer $CRM_TOKEN" \
  "https://analyst-assessment-production.up.railway.app/api/v1/accounts?q=bellhaven"
```

The token maps to an isolated copy of the CRM. Nothing can be broken, so explore freely.

## What to build

1. **A scraper** that pulls every Bellhaven location from the website (name, address,
   city, state, zip, care offerings).
2. **Matching logic** that links each location to the right CRM account and classifies
   it. Cases to handle include: a confident match; a match that needs a fix (for
   example a wrong parent account or an outdated name); a location that has no CRM
   account yet; and CRM accounts under the Bellhaven parent that no longer appear on
   the website.
3. **A small review app** (running locally is fine) where a reviewer sees each proposed
   change with its supporting evidence and can approve or reject it. Approved changes
   write back to the CRM through the API. **Nothing writes without approval.**
4. **Built to run daily.** Include the schedule configuration you would use (a cron
   entry or a GitHub Actions workflow file is fine, it does not need to be live).
   Re-runs must be safe: running the pipeline a second time must not re-propose items
   that were already decided.

## CRM conventions

Accounts have a `parent_id` linking facilities to their parent company account.

Some findings won't be simple field updates — you may conclude an account is a
duplicate, or shouldn't be under Bellhaven anymore. The tools for expressing those
outcomes are the `status` field (valid values: `Active`, `Inactive`, `Needs Review`)
and the free text `note` field. **There is no merge or delete in this API.**

If you conclude an account is a duplicate of another, set `duplicate_of_account` on the
losing copy to the surviving account's id and mark it `Inactive`. Explain your choices
in the writeup.

## The SOP you must follow

Some facilities have billing history with us, and every account carries
`lifetime_revenue` and `outstanding_ar` fields. When an account needs to move to a
different parent, check them first.

> **If the account has revenue history AND outstanding AR greater than zero**, our
> billing team needs the old account preserved, so you must **NOT** change its parent.
> Instead, leave the existing account exactly as it is, create a new account for the
> facility under the correct parent, and set `chow_current_account` on the OLD account
> to the new account's id. (CHOW stands for change of ownership.)
>
> **If the account has no revenue history, or no outstanding AR**, re-parent the
> existing account directly.

Handling this correctly matters as much as the matching itself.

## Definition of done

> Your finished submission is a corrected CRM, not just a tool. Before the deadline,
> actually run your pipeline, review the proposals in your app, and approve the ones
> you believe are right so the changes land in your CRM copy. We evaluate the end state
> of your data alongside the system that produced it. **A review queue full of
> unapproved proposals scores the same as doing nothing.**
