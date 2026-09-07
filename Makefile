PY := .venv/bin/python
#: Must match OFFLINE_DB_PATH in bellhaven/pipeline.py. `make run-offline`
#: writes here, so `make review` on the default ledger shows an empty queue.
OFFLINE_DB := data/ledger-offline.db

.PHONY: install test run run-offline review review-offline apply

install:
	python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'

test:
	.venv/bin/pytest -q

run:
	$(PY) -m bellhaven.pipeline

run-offline:
	$(PY) -m bellhaven.pipeline --offline

review:
	.venv/bin/uvicorn review.app:app --reload --port 8000

review-offline:
	BELLHAVEN_DB=$(OFFLINE_DB) .venv/bin/uvicorn review.app:app --reload --port 8000

apply:
	$(PY) -m bellhaven.apply
