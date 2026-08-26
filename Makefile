PY := .venv/bin/python

.PHONY: install test run run-offline review apply

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

apply:
	$(PY) -m bellhaven.apply
