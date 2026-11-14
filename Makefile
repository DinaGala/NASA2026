PYTHON ?= python3.14
VENV ?= .venv314
VENV_PYTHON := $(VENV)/bin/python

-include .env
export

.PHONY: setup install db-up schema import load-chunks fixtures api test test-live verify db-down

setup: install
	docker compose up -d --wait postgres
	$(VENV_PYTHON) scripts/apply_schema.py
	$(VENV_PYTHON) scripts/import_psi_data.py
	$(VENV_PYTHON) scripts/load_chunks.py

install:
	$(PYTHON) -m venv $(VENV)
	$(VENV_PYTHON) -m pip install --upgrade pip
	$(VENV_PYTHON) -m pip install -r requirements.txt
	$(VENV_PYTHON) -m pip install -e .

db-up:
	docker-compose up -d --wait postgres

schema:
	$(VENV_PYTHON) scripts/apply_schema.py

import:
	$(VENV_PYTHON) scripts/import_psi_data.py

load-chunks:
	$(VENV_PYTHON) scripts/load_chunks.py

fixtures:
	$(VENV_PYTHON) scripts/make_fixtures.py

api:
	$(VENV)/bin/uvicorn src.api.app:app --reload

test:
	$(VENV_PYTHON) -m unittest tests/test_*.py

# Calls the real LLM (a few cents): needs LLM_MODEL and its API key in .env.
test-live:
	LIVE_LLM_TESTS=1 $(VENV_PYTHON) -m unittest -v tests/live/test_verdict_live.py

verify:
	docker-compose exec postgres psql -U flame_user -d psi_flame -c "\\dt psi_dashboard.*"
	docker-compose exec postgres psql -U flame_user -d psi_flame -c "SELECT count(*) FROM psi_dashboard.investigations;"
	docker-compose exec postgres psql -U flame_user -d psi_flame -c "SELECT psi_id, acronym, title FROM psi_dashboard.investigations ORDER BY psi_id;"

db-down:
	docker-compose down
