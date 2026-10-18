PY ?= python3

.PHONY: check lint types test install cov

install:
	$(PY) -m pip install -e ".[dev]"

lint:
	$(PY) -m ruff check agent tests
	$(PY) -m ruff format --check agent tests

types:
	$(PY) -m mypy

test:
	$(PY) -m pytest

# The RiskGate is the last line of defence: every branch of agent/risk must be exercised.
cov:
	$(PY) -m pytest --cov=agent/risk --cov-branch --cov-report=term-missing --cov-fail-under=100
	$(PY) -m pytest -q --cov=agent/execution --cov=agent/live --cov=agent/ops --cov-branch --cov-fail-under=90

check: lint types test cov
