### NASA PSI Combustion Database

This project includes a PostgreSQL database schema and import scripts for the NASA PSI combustion dashboard data.

#### Setup

1.  **PostgreSQL Container**: Ensure the PostgreSQL container is running. The project is configured to connect to `localhost:5432` with database `flame_db`.
2.  **Environment**: Activate the virtual environment and set `PYTHONPATH`.
    ```bash
    source .venv/bin/activate
    export PYTHONPATH=$PYTHONPATH:.
    ```
3.  **Apply Schema**: Run the migration script to create the `psi_dashboard` schema and tables.
    ```bash
    python3 scripts/apply_schema.py
    ```
4.  **Import Data**: Run the seed script to import CSV data from `psi_data`.
    ```bash
    python3 scripts/import_psi_data.py
    ```

#### Database Model

The data is organized into the `psi_dashboard` schema with the following tables:

*   **`investigations`**: Metadata for each study (PSI-ID, name, metadata JSON).
*   **`source_files`**: Traceability for imported files.
*   **`runs`**: Run-level summaries (Date, GMT, Fuel, Flow Direction).
*   **`run_values`**: Individual measurements and observations per run. Ambiguous values are stored as text; numeric conversions are provided where safe.
*   **`design_ranges`**: Experimental design ranges for FLEX-2.

#### Data Caveats

*   **FLEX-2**: As specified, individual runs were not created for FLEX-2. Instead, the design ranges are stored in the `design_ranges` table.
*   **Ambiguous Values**: Values containing ranges (e.g., "~ 21.5", "21.5 - 21.7") or qualitative outcomes (e.g., "Complete", "Extinction") are preserved in the `text_value` field. The `numeric_value` field is `NULL` for these cases to avoid silent conversion errors.
*   **Units**: Units are extracted from column headers where possible.
*   **Idempotency**: The import script can be rerun without duplicating records. It updates existing investigations/runs and refreshes measurements.

#### Schema Namespace

All tables are created within the `psi_dashboard` schema to avoid conflicts with existing tables in `flame_db`.
