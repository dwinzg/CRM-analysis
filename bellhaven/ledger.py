"""SQLite proposal ledger. No business logic. This is what makes re-runs safe.

The contract the brief asks for is "running the pipeline a second time must not
re-propose items that were already decided". That reduces to giving every
proposed change a stable identity and never resurrecting a decided one.
"""
from __future__ import annotations

import datetime
import hashlib
import json
import os
import sqlite3

#: Statuses that represent a human (or a writeback) having settled the matter.
#: A proposal in any of these is never offered for review again.
DECIDED = ("approved", "rejected", "applied", "failed")

SCHEMA = """
CREATE TABLE IF NOT EXISTS proposals (
  fingerprint       TEXT PRIMARY KEY,
  kind              TEXT NOT NULL,
  target_account_id TEXT,
  site_slug         TEXT,
  changes_json      TEXT,
  new_account_json  TEXT,
  evidence_json     TEXT,
  confidence        INTEGER,
  status            TEXT NOT NULL,
  first_seen_at     TEXT,
  last_seen_at      TEXT,
  decided_at        TEXT,
  decided_note      TEXT,
  applied_at        TEXT,
  result_json       TEXT
);
CREATE INDEX IF NOT EXISTS idx_proposals_status ON proposals(status);
"""


def fingerprint(kind: str, target_id: str, changes: dict, new_account: dict | None) -> str:
    """Stable identity for a proposed change.

    `note` is excluded on purpose. Notes carry dates and prose ("not listed as
    of 2026-08-26"); including them would mint a fresh identity every day and a
    rejected proposal would return forever. Everything that actually alters the
    CRM is inside the hash, so a changed payload correctly becomes a new row.
    """
    payload = {
        "kind": kind,
        "target": target_id or "",
        "changes": {k: v for k, v in sorted((changes or {}).items()) if k != "note"},
        "new": {k: v for k, v in sorted((new_account or {}).items()) if k != "note"},
    }
    blob = json.dumps(payload, sort_keys=True, default=str)
    return hashlib.sha256(blob.encode()).hexdigest()[:16]


def _now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


class Ledger:
    def __init__(self, db_path: str):
        parent = os.path.dirname(db_path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        self.db = sqlite3.connect(db_path)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)

    # --- writes ----------------------------------------------------------

    def upsert(self, proposals) -> dict:
        """Record this run's proposals. Returns {inserted, seen, staled}.

        INSERT OR IGNORE is what enforces the re-run guarantee: a fingerprint
        already in the table keeps whatever status it has, decided or not.
        """
        seen: list[str] = []
        inserted = 0
        for p in proposals:
            fp = fingerprint(p.kind, p.target_account_id, p.changes, p.new_account)
            seen.append(fp)
            cur = self.db.execute(
                "INSERT OR IGNORE INTO proposals "
                "(fingerprint, kind, target_account_id, site_slug, changes_json, "
                " new_account_json, evidence_json, confidence, status, "
                " first_seen_at, last_seen_at) "
                "VALUES (?,?,?,?,?,?,?,?,'pending',?,?)",
                (fp, p.kind, p.target_account_id, p.site_slug,
                 json.dumps(p.changes), json.dumps(p.new_account),
                 json.dumps(p.evidence), p.confidence, _now(), _now()),
            )
            inserted += cur.rowcount
            self.db.execute("UPDATE proposals SET last_seen_at = ? WHERE fingerprint = ?",
                            (_now(), fp))
            # A proposal that went stale, or whose write failed, is still an
            # undecided and still-valid correction if the pipeline computes it
            # again. Revive it, or it stays invisible with no way back: a five
            # second CRM outage would otherwise retire every remaining
            # approved change. Only 'stale' and 'failed' are lifted, so an
            # approval or a rejection stays permanent.
            self.db.execute(
                "UPDATE proposals SET status = 'pending' "
                "WHERE fingerprint = ? AND status IN ('stale', 'failed')", (fp,))

        # A pending proposal that stopped appearing describes a world that no
        # longer exists. Decided rows are never touched.
        placeholders = ",".join("?" * len(seen)) if seen else "''"
        cur = self.db.execute(
            f"UPDATE proposals SET status = 'stale' "
            f"WHERE status = 'pending' AND fingerprint NOT IN ({placeholders})",
            seen,
        )
        staled = cur.rowcount
        self.db.commit()
        return {"inserted": inserted, "seen": len(seen), "staled": staled}

    def decide(self, fp: str, decision: str, note: str = "") -> None:
        """Record a human decision.

        Refuses to touch an already-applied row. `/decide` is a plain form POST,
        so a back-button resubmit would otherwise re-approve an applied CREATE
        and the next apply would make a second account for one facility.
        A failed row can be re-approved, because failure is a transport problem
        rather than a verdict on the change.
        """
        if decision not in ("approved", "rejected"):
            raise ValueError(f"decision must be approved or rejected, got {decision!r}")
        row = self.db.execute(
            "SELECT status FROM proposals WHERE fingerprint = ?", (fp,)).fetchone()
        if row is None:
            raise KeyError(f"no proposal with fingerprint {fp!r}")
        if row["status"] == "applied":
            raise ValueError(f"proposal {fp} is already applied and cannot be re-decided")
        self.db.execute(
            "UPDATE proposals SET status = ?, decided_at = ?, decided_note = ? "
            "WHERE fingerprint = ?", (decision, _now(), note, fp))
        self.db.commit()

    def mark_applied(self, fp: str, result: dict) -> None:
        self.db.execute(
            "UPDATE proposals SET status = 'applied', applied_at = ?, result_json = ? "
            "WHERE fingerprint = ?", (_now(), json.dumps(result), fp))
        self.db.commit()

    def mark_failed(self, fp: str, reason: str) -> None:
        self.db.execute(
            "UPDATE proposals SET status = 'failed', applied_at = ?, result_json = ? "
            "WHERE fingerprint = ?", (_now(), json.dumps({"error": reason}), fp))
        self.db.commit()

    # --- reads -----------------------------------------------------------

    def _rows(self, where: str = "", args=()) -> list[dict]:
        return [dict(r) for r in self.db.execute(
            f"SELECT * FROM proposals {where} ORDER BY kind, confidence DESC, target_account_id",
            args)]

    def pending(self) -> list[dict]:
        return self._rows("WHERE status = 'pending'")

    def approved(self) -> list[dict]:
        return self._rows("WHERE status = 'approved'")

    def all_rows(self) -> list[dict]:
        return self._rows()

    def counts(self) -> dict:
        return {r["status"]: r["n"] for r in self.db.execute(
            "SELECT status, COUNT(*) AS n FROM proposals GROUP BY status")}
