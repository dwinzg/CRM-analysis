"""Local review queue. The only way a proposal becomes approved.

Deliberately server-rendered with no build step: it has to start with one
command during a live walkthrough, and a reviewer has to be able to read the
evidence without trusting the tool that produced it.
"""
from __future__ import annotations

import json

from fastapi import FastAPI, Form, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

from bellhaven import config
from bellhaven.apply import apply_approved
from bellhaven.ledger import Ledger

app = FastAPI(title="Bellhaven CRM Review")
templates = Jinja2Templates(directory="review/templates")

#: Highest-stakes first. A reviewer's attention is the scarce resource, and a
#: mistaken CHOW or duplicate costs far more than a mistaken rename.
KIND_ORDER = ["CHOW", "DUPLICATE", "REPARENT", "DELISTED", "CREATE", "RENAME", "FIELD_FIX"]

KIND_BLURB = {
    "CHOW": "Revenue AND outstanding AR are both above zero, so billing needs the "
            "existing record frozen. The old account keeps its parent and every "
            "business field; a successor is created under Bellhaven and linked back.",
    "DUPLICATE": "Several CRM accounts describe one building. This API has no merge "
                 "or delete, so the losing copies get duplicate_of_account set to the "
                 "survivor and are marked Inactive.",
    "REPARENT": "The facility is listed on the Bellhaven site but hangs off the wrong "
                "parent. Outstanding AR is zero, so the SOP allows re-parenting in place.",
    "DELISTED": "Under the Bellhaven parent but absent from the website. Inactive only "
                "where another operator holds the identical address; otherwise flagged "
                "for a human rather than asserted.",
    "CREATE": "Listed on the website with no CRM account anywhere in that city.",
    "RENAME": "Matched confidently on address, but the CRM name is out of date.",
    "FIELD_FIX": "Address or care-type drift against the public listing.",
}


def _ledger() -> Ledger:
    return Ledger(config.DB_PATH)


def _hydrate(rows: list[dict]) -> list[dict]:
    for r in rows:
        r["changes"] = json.loads(r["changes_json"] or "{}")
        r["evidence"] = json.loads(r["evidence_json"] or "{}")
        r["new_account"] = json.loads(r["new_account_json"] or "null")
        r["result"] = json.loads(r["result_json"] or "null")
    return rows


@app.get("/")
def index(request: Request, show: str = "pending"):
    led = _ledger()
    rows = _hydrate(led.pending() if show == "pending" else led.all_rows())
    groups = [(k, [r for r in rows if r["kind"] == k]) for k in KIND_ORDER]
    groups = [(k, v) for k, v in groups if v]
    return templates.TemplateResponse(request, "index.html", {
        "groups": groups,
        "counts": led.counts(),
        "show": show,
        "blurb": KIND_BLURB,
        "total": len(rows),
    })


@app.post("/decide")
def decide(fingerprint: str = Form(...), decision: str = Form(...), note: str = Form("")):
    _ledger().decide(fingerprint, decision, note)
    return RedirectResponse("/", status_code=303)


@app.post("/decide_bulk")
def decide_bulk(kind: str = Form(...), decision: str = Form(...)):
    led = _ledger()
    for row in led.pending():
        if row["kind"] == kind:
            led.decide(row["fingerprint"], decision, f"bulk {decision} of all {kind}")
    return RedirectResponse("/", status_code=303)


@app.post("/apply")
def apply_now():
    apply_approved()
    return RedirectResponse("/?show=all", status_code=303)
