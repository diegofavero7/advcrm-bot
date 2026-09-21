.PHONY: install lint format typecheck test schemas check run

PYTHON ?= .venv/bin/python
PIP ?= .venv/bin/pip

install:
	python3.12 -m venv .venv
	$(PIP) install -U pip
	$(PIP) install -e ".[dev]"

lint:
	$(PYTHON) -m ruff check .

format:
	$(PYTHON) -m ruff format .

format-check:
	$(PYTHON) -m ruff format --check .

typecheck:
	$(PYTHON) -m mypy app

test:
	$(PYTHON) -m pytest

schemas:
	$(PYTHON) scripts/export_json_schemas.py

check: format-check lint typecheck test schemas

run:
	$(PYTHON) -m uvicorn app.main:app --host 0.0.0.0 --port 8080 --reload
