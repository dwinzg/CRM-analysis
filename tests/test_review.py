"""Review app tests. The gate is the point: only this app can approve, and
only approval reaches the CRM.
"""
import pytest
from fastapi.testclient import TestClient

from bellhaven import config
from bellhaven.classify import Proposal
from bellhaven.ledger import Ledger, fingerprint


@pytest.fixture
def client(tmp_path, monkeypatch):
    db = str(tmp_path / "review.db")
    monkeypatch.setattr(config, "DB_PATH", db)
    import review.app as app_module
    monkeypatch.setattr(app_module.config, "DB_PATH", db)

    led = Ledger(db)
    led.upsert([
        Proposal("CHOW", "OLD1", "bellhaven-of-tiffin",
                 {"chow_current_account": "<successor>", "note": "chow note"},
                 {"tier": "A", "signals": ["street+zip"],
                  "site": {"name": "Bellhaven of Tiffin", "address": "45 St Lawrence Dr, Tiffin, OH 44883",
                           "care": ["Short-Term Rehabilitation & Nursing"], "phone": "", "url": "http://x"},
                  "crm": {"account_id": "OLD1", "name": "Bellhaven of Tiffin", "address": "a",
                          "parent": "Cedar Trail", "status": "Active", "care_type": "Skilled Nursing",
                          "lifetime_revenue": 84000, "outstanding_ar": 12400}},
                 100, {"name": "Bellhaven of Tiffin", "parent_id": config.BELLHAVEN_PARENT_ID}),
        Proposal("RENAME", "A2", "s", {"name": "New"},
                 {"tier": "A", "signals": [],
                  "crm": {"account_id": "A2", "name": "Old", "address": "b", "parent": "p",
                          "status": "Active", "care_type": "", "lifetime_revenue": 0,
                          "outstanding_ar": 0}}, 100),
    ])
    yield TestClient(__import__("review.app", fromlist=["app"]).app), led


def test_pending_page_renders_evidence_and_sop(client):
    c, _ = client
    body = c.get("/").text
    assert "Billing SOP" in body
    assert "84,000" in body and "12,400" in body, "revenue and AR must be visible"
    assert "Tier A" in body
    assert "chow_current_account" in body


def test_nothing_is_approved_until_a_human_acts(client):
    _, led = client
    assert led.approved() == []
    assert len(led.pending()) == 2


def test_decide_approves_a_single_row(client):
    c, led = client
    fp = led.pending()[0]["fingerprint"]
    r = c.post("/decide", data={"fingerprint": fp, "decision": "approved", "note": "checked"},
               follow_redirects=False)
    assert r.status_code == 303
    row = next(x for x in led.all_rows() if x["fingerprint"] == fp)
    assert row["status"] == "approved"
    assert row["decided_note"] == "checked"


def test_reject_records_the_reason(client):
    c, led = client
    fp = led.pending()[0]["fingerprint"]
    c.post("/decide", data={"fingerprint": fp, "decision": "rejected", "note": "evidence too thin"},
           follow_redirects=False)
    row = next(x for x in led.all_rows() if x["fingerprint"] == fp)
    assert row["status"] == "rejected"
    assert row["decided_note"] == "evidence too thin"


def test_bulk_only_touches_its_own_kind(client):
    c, led = client
    c.post("/decide_bulk", data={"kind": "RENAME", "decision": "approved"},
           follow_redirects=False)
    by_kind = {r["kind"]: r["status"] for r in led.all_rows()}
    assert by_kind["RENAME"] == "approved"
    assert by_kind["CHOW"] == "pending"


def test_apply_button_writes_only_approved(client, monkeypatch):
    c, led = client
    written = []

    class Spy:
        def get_account(self, aid):
            return {"account_id": aid, "name": "Old", "parent_id": "CEDAR",
                    "chow_current_account": ""}

        def patch_account(self, aid, fields):
            written.append(("patch", aid, fields))
            return {"account_id": aid, "message": "updated"}

        def create_account(self, fields):
            written.append(("create", None, fields))
            return {"account_id": "NEW1", "message": "created"}

    import bellhaven.apply as apply_module
    monkeypatch.setattr(apply_module, "CrmClient", lambda *a, **k: Spy())

    # Approve only the RENAME; the CHOW stays pending.
    c.post("/decide_bulk", data={"kind": "RENAME", "decision": "approved"},
           follow_redirects=False)
    c.post("/apply", follow_redirects=False)

    assert len(written) == 1, "only the approved row may be written"
    kind, aid, fields = written[0]
    assert kind == "patch" and aid == "A2" and fields["name"] == "New"


def test_all_view_shows_decided_rows(client):
    c, led = client
    fp = led.pending()[0]["fingerprint"]
    c.post("/decide", data={"fingerprint": fp, "decision": "rejected", "note": "no"},
           follow_redirects=False)
    assert "rejected" in c.get("/?show=all").text


@pytest.fixture
def empty_client(tmp_path, monkeypatch):
    """A ledger that exists but was never populated, which is what you get
    when `make run-offline` wrote elsewhere."""
    db = str(tmp_path / "empty.db")
    monkeypatch.setattr(config, "DB_PATH", db)
    import review.app as app_module
    monkeypatch.setattr(app_module.config, "DB_PATH", db)
    Ledger(db)
    yield TestClient(__import__("review.app", fromlist=["app"]).app), db


def test_empty_ledger_does_not_claim_everything_was_decided(empty_client):
    """The old message read as 'the tool ran and found nothing', which is a
    different and much worse statement than 'the tool has not run'."""
    c, _ = empty_client
    body = c.get("/").text
    assert "Every proposal has been decided" not in body
    assert "No proposals" in body


def test_empty_ledger_names_the_database_it_read(empty_client):
    """Pointing the app at the wrong ledger is the failure this message exists
    to explain, so it has to say which file it looked in."""
    c, db = empty_client
    assert db in c.get("/").text


def test_all_decided_still_says_everything_was_decided(client):
    c, led = client
    for row in led.pending():
        led.decide(row["fingerprint"], "approved")
    assert "Every proposal has been decided" in c.get("/").text
