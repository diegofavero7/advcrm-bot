.PHONY: install lint format typecheck test schemas check run eval-offline eval-compare-offline \
	experiment-signals-prepare experiment-signals-train-stub experiment-signals-eval-stub \
	install-setfit-experiment

PYTHON ?= .venv/bin/python
PIP ?= .venv/bin/pip

install:
	python3.12 -m venv .venv
	$(PIP) install -U pip
	$(PIP) install -e ".[dev]"

# Extra opcional — não faz parte de `make install` / CI operacional.
install-setfit-experiment:
	$(PIP) install -e ".[setfit-experiment]"

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

eval-offline:
	$(PYTHON) scripts/generate_eval_cases.py
	$(PYTHON) -m evaluations.runner

# Comparação de understanding — offline com respostas sintéticas (sem rede).
eval-compare-offline:
	$(PYTHON) scripts/generate_compare_cases.py
	$(PYTHON) -m evaluations.compare run --variant baseline \
		--synthetic evaluations/compare/synthetic/baseline_responses.json \
		--out artifacts/evaluations/latest_compare_baseline_offline
	$(PYTHON) -m evaluations.compare run --variant candidate \
		--synthetic evaluations/compare/synthetic/candidate_responses.json \
		--out artifacts/evaluations/latest_compare_candidate_offline
	$(PYTHON) -m evaluations.compare diff \
		--baseline artifacts/evaluations/latest_compare_baseline_offline \
		--candidate artifacts/evaluations/latest_compare_candidate_offline \
		--out artifacts/evaluations/latest_compare_diff_offline

check: format-check lint typecheck test schemas

# Experimento SetFit (sinais de atendimento) — stub/offline; sem download.
experiment-signals-prepare:
	$(PYTHON) -m experiments.attendance_signals_setfit prepare-data

experiment-signals-train-stub:
	$(PYTHON) -m experiments.attendance_signals_setfit train

experiment-signals-eval-stub:
	$(PYTHON) -m experiments.attendance_signals_setfit evaluate --split test

run:
	$(PYTHON) -m uvicorn app.main:app --host 0.0.0.0 --port 8080 --reload
