import os
import json
import argparse
from typing import List, Dict, Any, Tuple
from sqlalchemy import text, inspect
from src.database import engine

# Constants for schema verification
SCHEMA_NAME = "psi_dashboard"
REQUIRED_TABLES = ["investigations", "runs", "measurements"]
RUNS_COLUMNS = [
    "test_id", "psi_id", "source_file", "run_label", "granularity",
    "flame_config", "fuel", "fuel_phase", "material", "geometry",
    "size_mm", "o2_frac", "o2_is_ambient", "pressure_atm", "diluent",
    "diluent_frac", "flow_cm_s", "gravity", "outcome", "outcome_source",
    "notes", "raw"
]

def check_schema():
    """Verify required tables and columns exist in the database."""
    inspector = inspect(engine)
    existing_tables = inspector.get_table_names(schema=SCHEMA_NAME)
    
    missing_tables = [t for t in REQUIRED_TABLES if t not in existing_tables]
    if missing_tables:
        raise RuntimeError(f"Missing required tables in schema '{SCHEMA_NAME}': {', '.join(missing_tables)}")
    
    columns = {col['name'] for col in inspector.get_columns("runs", schema=SCHEMA_NAME)}
    missing_columns = [c for c in RUNS_COLUMNS if c not in columns]
    if missing_columns:
        # Check if they are important for our current load
        # For now, if any are missing from our list, we report it.
        raise RuntimeError(f"Missing required columns in '{SCHEMA_NAME}.runs': {', '.join(missing_columns)}")

def get_existing_psi_ids() -> set:
    """Return set of investigation IDs already in the database."""
    with engine.connect() as conn:
        result = conn.execute(text(f"SELECT psi_id FROM {SCHEMA_NAME}.investigations"))
        return {row[0] for row in result}

UPSERT_RUN = text(f"""
    INSERT INTO {SCHEMA_NAME}.runs (
        test_id, psi_id, source_file, run_label, granularity,
        flame_config, fuel, o2_frac, o2_is_ambient, pressure_atm, 
        diluent, diluent_frac, outcome, notes, raw
    ) VALUES (
        :test_id, :psi_id, :source_file, :run_label, :granularity,
        :flame_config, :fuel, :o2_frac, :o2_is_ambient, :pressure_atm,
        :diluent, :diluent_frac, :outcome, :notes, CAST(:raw AS JSONB)
    )
    ON CONFLICT (test_id) DO UPDATE SET
        psi_id = EXCLUDED.psi_id,
        source_file = EXCLUDED.source_file,
        run_label = EXCLUDED.run_label,
        granularity = EXCLUDED.granularity,
        flame_config = EXCLUDED.flame_config,
        fuel = EXCLUDED.fuel,
        o2_frac = EXCLUDED.o2_frac,
        o2_is_ambient = EXCLUDED.o2_is_ambient,
        pressure_atm = EXCLUDED.pressure_atm,
        diluent = EXCLUDED.diluent,
        diluent_frac = EXCLUDED.diluent_frac,
        outcome = EXCLUDED.outcome,
        notes = EXCLUDED.notes,
        raw = EXCLUDED.raw
""")

DELETE_MEASUREMENTS = text(f"DELETE FROM {SCHEMA_NAME}.measurements WHERE test_id = :test_id")

INSERT_MEASUREMENT = text(f"""
    INSERT INTO {SCHEMA_NAME}.measurements (test_id, variable, value, unit)
    VALUES (:test_id, :variable, :value, :unit)
""")

def load_file(file_path: str, dry_run: bool, existing_psi_ids: set) -> Dict[str, Any]:
    with open(file_path, "r") as f:
        records = json.load(f)
    
    report = {
        "file": file_path,
        "total_records": len(records),
        "eligible_loaded": 0,
        "skipped_ineligible": 0,
        "skipped_missing_investigation": 0,
        "measurements_inserted": 0,
        "errors": []
    }
    
    run_batch = []
    meas_batch = []
    test_ids_to_clear = []

    for rec in records:
        if not rec.get("is_eligible", True):
            report["skipped_ineligible"] += 1
            continue
        
        psi_id = rec.get("psi_id")
        if psi_id not in existing_psi_ids:
            report["skipped_missing_investigation"] += 1
            report["errors"].append(f"Missing investigation {psi_id} for test {rec.get('test_id')}")
            continue

        # Prepare run record
        run_data = {
            "test_id": rec["test_id"],
            "psi_id": psi_id,
            "source_file": rec.get("source_file"),
            "run_label": rec.get("run_label"),
            "granularity": rec.get("granularity"),
            "flame_config": rec.get("flame_config"),
            "fuel": rec.get("fuel"),
            "o2_frac": rec.get("o2_frac"),
            "o2_is_ambient": rec.get("o2_is_ambient"),
            "pressure_atm": rec.get("pressure_atm"),
            "diluent": rec.get("diluent"),
            "diluent_frac": rec.get("diluent_frac"),
            "outcome": rec.get("outcome"),
            "notes": rec.get("notes"),
            "raw": json.dumps(rec.get("raw", {}), ensure_ascii=False)
        }
        
        # Verify Inverse O2 safety
        if rec.get("flame_config") == "gas_inverse" and rec.get("o2_is_ambient") is True:
             raise ValueError(f"Safety Check Failed: Inverse CFI-G oxygen marked as ambient in {rec['test_id']}")

        run_batch.append(run_data)
        test_ids_to_clear.append({"test_id": rec["test_id"]})
        
        for m in rec.get("measurements", []):
            meas_batch.append({
                "test_id": rec["test_id"],
                "variable": m["variable"],
                "value": m["value"],
                "unit": m["unit"]
            })
    
    if not dry_run and run_batch:
        with engine.begin() as conn:
            # We use engine.begin() for a transaction
            for run in run_batch:
                conn.execute(UPSERT_RUN, run)
            
            # Clear and re-insert measurements for these test_ids
            # Doing it per test_id or in bulk. Bulk delete is faster.
            # But we only want to delete for the test_ids we are touching.
            for tid_dict in test_ids_to_clear:
                conn.execute(DELETE_MEASUREMENTS, tid_dict)
            
            if meas_batch:
                conn.execute(INSERT_MEASUREMENT, meas_batch)
                
    report["eligible_loaded"] = len(run_batch)
    report["measurements_inserted"] = len(meas_batch)
    
    return report

def main():
    parser = argparse.ArgumentParser(description="Load normalized PSI data into the database.")
    parser.add_argument("--dry-run", action="store_true", help="Validate data and schema without writing.")
    args = parser.parse_args()

    print("Checking database schema...")
    try:
        check_schema()
    except Exception as e:
        print(f"Schema Error: {e}")
        return

    existing_psi_ids = get_existing_psi_ids()
    
    processed_dir = "data/processed"
    files = [
        os.path.join(processed_dir, "PSI-69_FLEX_normalized.json"),
        os.path.join(processed_dir, "PSI-159_CFI-G_Normal_normalized.json"),
        os.path.join(processed_dir, "PSI-159_CFI-G_Inverse_normalized.json")
    ]
    
    total_report = []
    for f_path in files:
        if not os.path.exists(f_path):
            print(f"Warning: File not found: {f_path}")
            continue
        print(f"Processing {f_path}...")
        report = load_file(f_path, args.dry_run, existing_psi_ids)
        total_report.append(report)

    print("\n--- Import Report ---")
    if args.dry_run:
        print("DRY RUN - No changes made to the database.\n")
    
    for r in total_report:
        print(f"File: {r['file']}")
        print(f"  Total records: {r['total_records']}")
        print(f"  Eligible loaded: {r['eligible_loaded']}")
        print(f"  Skipped (ineligible): {r['skipped_ineligible']}")
        print(f"  Skipped (missing investigation): {r['skipped_missing_investigation']}")
        print(f"  Measurements inserted: {r['measurements_inserted']}")
        if r["errors"]:
            print(f"  Errors: {len(r['errors'])}")
            for err in r["errors"][:5]:
                print(f"    - {err}")
    
    if any(r["skipped_missing_investigation"] > 0 for r in total_report):
        print("\nNote: Some records were skipped because their investigation ID was not found.")
        print("Run 'make import' to ensure all investigations are present.")

if __name__ == "__main__":
    main()
