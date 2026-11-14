# NASA Flame in Freefall

This project is building a dashboard around NASA Physical Sciences Informatics (PSI) combustion studies. The current database milestone creates a local PostgreSQL database, applies the shared schema, and imports investigation metadata only.

## Prerequisites

- Python 3
- Docker Desktop, or Docker Engine with Docker Compose (for example, Colima on macOS)
- The PSI investigation metadata under `psi_data/PSI-*/investigation.json`

## Configure the database

Create or update `.env` in the project root. Use the same credentials for the container and Python scripts:

```dotenv
POSTGRES_USER=flame_user
POSTGRES_PASSWORD=your_local_password
POSTGRES_DB=psi_flame
POSTGRES_PORT=5432
```

Do not commit `.env`. Keep `POSTGRES_DB` set to `psi_flame`; an old `flame_db` value will make the Python scripts connect to the wrong database.

## Quick setup

With Docker running and `.env` configured, run:

```bash
make setup
```

This creates `.venv`, installs Python dependencies from `requirements.txt`, starts the PostgreSQL container, waits for it to become healthy, applies the schema, and imports investigation metadata.

## Individual Make targets

| Command | Action |
|---|---|
| `make install` | Create `.venv` and install Python dependencies |
| `make db-up` | Pull/start the PostgreSQL service and wait until healthy |
| `make schema` | Create the database schema and tables |
| `make import` | Upsert PSI investigations from `psi_data` |
| `make chunks` | Rebuild `data/processed/chunks.parquet`: split the PSI documents into page chunks and embed them with bge-small (10–15 min; only when the documents or chunking change) |
| `make load-chunks` | Truncate and reload `psi_dashboard.chunks` from the committed parquet (seconds, no re-embedding) |
| `make fixtures` | Regenerate the API fixtures in `data/fixtures/` from the FLEX and BASS-II CSVs |
| `make api` | Serve the fixture-backed API at http://127.0.0.1:8000 (docs at `/docs`; contracts guide in [`docs/api_contracts.md`](docs/api_contracts.md)) |
| `make test` | Run the unit tests |
| `make verify` | List tables, count investigations, and list study IDs |
| `make db-down` | Stop the PostgreSQL container while preserving its data volume |

Equivalent manual commands:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m pip install -e .
docker compose up -d --wait postgres
.venv/bin/python scripts/apply_schema.py
.venv/bin/python scripts/import_psi_data.py
```

## Database schema and current data

The database is named `psi_flame`. Its tables live in the `psi_dashboard` schema and follow [`data_audit.md`](data_audit.md):

- **`investigations`** — one PSI study per row, with acronym, title, pillar, fuel phase, hardware, objective, hypothesis, approach, impacts, DOI, dates, and original metadata in `raw` JSONB.
- **`runs`** — experiment/test-level context, keyed by `test_id` and linked to its investigation by `psi_id`.
- **`measurements`** — numeric variable/value/unit rows linked to a run by `test_id`.

The schema setup creates all three tables. The importer currently populates only `investigations`, keyed by `psi_id`; `runs` and `measurements` remain empty for a later milestone. The investigation import is safe to rerun because it upserts existing records. Reapplying the schema renames an existing `scenarios` table to `runs` and preserves its data and foreign-key relationships.

When all local metadata files are present, the expected number of investigation rows is 24.

## Manual database verification

`make verify` runs these checks:

```bash
docker compose exec postgres psql -U flame_user -d psi_flame -c "\\dt psi_dashboard.*"
docker compose exec postgres psql -U flame_user -d psi_flame -c "SELECT count(*) FROM psi_dashboard.investigations;"
docker compose exec postgres psql -U flame_user -d psi_flame -c "SELECT psi_id, acronym, title FROM psi_dashboard.investigations ORDER BY psi_id;"
```

## Downloading PSI data

The repository includes PSI downloader utilities in `scripts/` and `src/ingestion/`. The database import step expects metadata already downloaded under `psi_data/`. The data directory is local input and should not be added to Git.
