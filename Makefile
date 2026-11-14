PYTHON ?= python3
VENV := .venv
VENV_PYTHON := $(VENV)/bin/python

.PHONY: setup install db-up schema import verify db-down

setup: install
	docker compose up -d --wait postgres
	$(VENV_PYTHON) scripts/apply_schema.py
	$(VENV_PYTHON) scripts/import_psi_data.py

install:
	$(PYTHON) -m venv $(VENV)
	$(VENV_PYTHON) -m pip install --upgrade pip
	$(VENV_PYTHON) -m pip install -r requirements.txt

db-up:
	docker-compose up -d --wait postgres

schema:
	$(VENV_PYTHON) scripts/apply_schema.py

import:
	$(VENV_PYTHON) scripts/import_psi_data.py

verify:
	docker-compose exec postgres psql -U flame_user -d psi_flame -c "\\dt psi_dashboard.*"
	docker-compose exec postgres psql -U flame_user -d psi_flame -c "SELECT count(*) FROM psi_dashboard.investigations;"
	docker-compose exec postgres psql -U flame_user -d psi_flame -c "SELECT psi_id, acronym, title FROM psi_dashboard.investigations ORDER BY psi_id;"

db-down:
	docker-compose down
