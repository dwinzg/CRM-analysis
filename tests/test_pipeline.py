"""Pipeline wiring tests."""
import json

from bellhaven import config
from bellhaven.ledger import Ledger
from bellhaven.pipeline import ledger_path_for, run


def test_offline_run_does_not_touch_the_live_ledger():
    """README tells people to use --offline to try the pipeline without
    touching the network. If that writes to the live ledger it replaces the
    real queue with the fixture queue and marks every live proposal stale."""
    assert ledger_path_for(offline=True, db_path=None) != config.DB_PATH
    assert ledger_path_for(offline=False, db_path=None) == config.DB_PATH


def test_explicit_db_path_always_wins():
    assert ledger_path_for(offline=True, db_path="/tmp/x.db") == "/tmp/x.db"
    assert ledger_path_for(offline=False, db_path="/tmp/x.db") == "/tmp/x.db"


def test_offline_run_is_self_contained(tmp_path):
    out = run(offline=True, db_path=str(tmp_path / "l.db"))
    assert out["source"] == "fixtures"
    assert out["locations_scraped"] == 35
    assert out["accounts_fetched"] == 121
    assert out["proposals"] == out["seen"]
    assert out["inserted"] == out["proposals"]


def test_offline_run_twice_inserts_nothing_new(tmp_path):
    db = str(tmp_path / "l.db")
    first = run(offline=True, db_path=db)
    second = run(offline=True, db_path=db)
    assert first["inserted"] > 0
    assert second["inserted"] == 0
    assert second["staled"] == 0


def test_makefile_offline_db_matches_the_pipeline():
    """`make review-offline` hardcodes the path pipeline.py writes to. If the
    two drift, the review app opens on an empty queue and the tool looks like
    it found nothing."""
    import pathlib
    import re

    from bellhaven.pipeline import OFFLINE_DB_PATH

    makefile = pathlib.Path(__file__).resolve().parents[1] / "Makefile"
    declared = re.search(r"^OFFLINE_DB\s*:=\s*(\S+)", makefile.read_text(), re.M)
    assert declared, "Makefile must declare OFFLINE_DB"
    assert declared.group(1) == OFFLINE_DB_PATH
