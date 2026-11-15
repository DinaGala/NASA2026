# NASA Flame in Freefall

This project is building a dashboard around NASA Physical Sciences Informatics (PSI) combustion studies. The current database milestone creates a local PostgreSQL database, applies the shared schema, and imports investigation metadata only.

## Prerequisites

- Python 3.14 (installed as `python3.14`)
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
source .venv314/bin/activate
make setup
```

This creates `.venv314` with Python 3.14, installs Python dependencies from `requirements.txt`, starts the PostgreSQL container, waits for it to become healthy, applies the schema, and imports investigation metadata.

## Individual Make targets

| Command | Action |
|---|---|
| `make install` | Create `.venv314` with Python 3.14 and install Python dependencies |
| `make db-up` | Pull/start the PostgreSQL service and wait until healthy |
| `make schema` | Create the database schema and tables |
| `make import` | Upsert PSI investigations from `psi_data` |
| `make load-chunks` | Truncate and reload `psi_dashboard.chunks` and its BM25 tables from the committed parquet (~25 s, no re-embedding); how the paper search works: [`docs/paper_search.md`](docs/paper_search.md) |
| `make fixtures` | Regenerate the API fixtures in `data/fixtures/` from the FLEX and BASS-II CSVs |
| `make api` | Serve the fixture-backed API at http://127.0.0.1:8000 (docs at `/docs`; contracts guide in [`docs/api_contracts.md`](docs/api_contracts.md)) |
| `make frontend` | Open the Streamlit dashboard (needs `make api` running; `API_URL` overrides `http://localhost:8000`); spec in [`docs/frontend_spec.md`](docs/frontend_spec.md) |
| `make preset-verdicts` | Recompute the 4 preset verdicts into `data/presets/` through `/verdict` (real LLM, a few cents; needs `make api`). Rerun when the prompt or the data changes |
| `make test` | Run the unit tests (no LLM calls) |
| `make test-live` | Run the verdict agent against the real LLM (a few cents; needs `LLM_MODEL` and its API key in `.env`; how it works: [`docs/verdict_agent.md`](docs/verdict_agent.md)) |
| `make verify` | List tables, count investigations, and list study IDs |
| `make db-down` | Stop the PostgreSQL container while preserving its data volume |

Equivalent manual commands:

```bash
python3.14 -m venv .venv314
.venv314/bin/python -m pip install --upgrade pip
.venv314/bin/python -m pip install -r requirements.txt
.venv314/bin/python -m pip install -e .
docker compose up -d --wait postgres
.venv314/bin/python scripts/apply_schema.py
.venv314/bin/python scripts/import_psi_data.py
PYTHONPATH=. .venv314/bin/python scripts/parse_psi_data.py
PYTHONPATH=. .venv314/bin/python scripts/load_psi_data.py --dry-run
PYTHONPATH=. .venv314/bin/python scripts/load_psi_data.py
.venv314/bin/python scripts/load_chunks.py
```

### Rebuilding the embedded chunks (maintainers only)

`data/processed/chunks.parquet` is committed: it holds every chunk with its embedding, and `make load-chunks` loads it in about 25 seconds. Rebuild it only when the documents in `psi_data/`, the chunking, or the embedding model change:

```bash
.venv314/bin/python scripts/make_chunks.py   # asks for confirmation; 10–15 min
```

It re-embeds everything and overwrites the parquet. Commit the new file, then everyone runs `make load-chunks`. To undo an accidental run: `git checkout data/processed/chunks.parquet`.

## Database schema and current data

The database is named `psi_flame`. Its tables live in the `psi_dashboard` schema and follow [`data_audit.md`](data/data_audit.md):

- **`investigations`** — one PSI study per row, with acronym, title, pillar, fuel phase, hardware, objective, hypothesis, approach, impacts, DOI, dates, and original metadata in `raw` JSONB.
- **`runs`** — experiment/test-level context, keyed by `test_id` and linked to its investigation by `psi_id`.
- **`measurements`** — numeric variable/value/unit rows linked to a run by `test_id`.

The schema setup creates all three tables. The importer populates `investigations` (keyed by `psi_id`), `runs` (keyed by `test_id`), and `measurements` (linked to `runs`). The import process is safe to rerun because it upserts existing records. Reapplying the schema renames an existing `scenarios` table to `runs` and preserves its data and foreign-key relationships.

The initial source mappings for FLEX and CFI-G are documented in [`docs/source_mappings.md`](docs/source_mappings.md). They capture input encodings, source row filtering, field mappings, and unit rules before parser implementation.

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
