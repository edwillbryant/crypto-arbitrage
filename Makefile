PY ?= python3

.PHONY: check lint types test install

install:
	$(PY) -m pip install -e ".[dev]"

lint:
	$(PY) -m ruff check agent tests
	$(PY) -m ruff format --check agent tests

types:
	$(PY) -m mypy

test:
	$(PY) -m pytest -m "not integration"

check: lint types test
