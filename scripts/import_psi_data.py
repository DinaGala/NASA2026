"""Idempotently populate only psi_dashboard.investigations from PSI metadata."""

import json
from datetime import datetime
from pathlib import Path
from typing import Optional

from sqlalchemy import text

from src.database import engine


DATA_DIR = Path(__file__).resolve().parents[1] / "psi_data"


def parse_date(value: Optional[str]):
    if not value:
        return None
    for date_format in ("%m-%d-%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(value, date_format).date()
        except ValueError:
            pass
    return None


def investigation_values(path: Path) -> dict:
    with path.open(encoding="utf-8") as source:
        record = json.load(source)

    psi_id = record.get("accession")
    title = record.get("title")
    if not psi_id or not title:
        raise ValueError(f"Missing accession or title in {path}")

    return {
        "psi_id": psi_id,
        "acronym": record.get("investigationAcronym") or None,
        "title": title,
        # Use the source's sub-research area as the pillar/category.
        "pillar": record.get("subResearchArea") or record.get("researchArea") or None,
        # Fuel phase is not a consistent top-level field in PSI metadata.
        "fuel_phase": record.get("fuelPhase") or None,
        "hardware": record.get("experimentHardware") or None,
        "objective": record.get("objective") or None,
        "hypothesis": record.get("hypothesis") or None,
        "approach": record.get("approach") or None,
        "impacts": record.get("researchImpacts") or None,
        "doi": record.get("doi") or None,
        "start_date": parse_date(record.get("investigationStartDate")),
        "end_date": parse_date(record.get("investigationEndDate")),
        "raw": json.dumps(record, ensure_ascii=False),
    }


UPSERT_INVESTIGATION = text("""
    INSERT INTO psi_dashboard.investigations (
        psi_id, acronym, title, pillar, fuel_phase, hardware, objective,
        hypothesis, approach, impacts, doi, start_date, end_date, raw
    ) VALUES (
        :psi_id, :acronym, :title, :pillar, :fuel_phase, :hardware, :objective,
        :hypothesis, :approach, :impacts, :doi, :start_date, :end_date,
        CAST(:raw AS JSONB)
    )
    ON CONFLICT (psi_id) DO UPDATE SET
        acronym = EXCLUDED.acronym,
        title = EXCLUDED.title,
        pillar = EXCLUDED.pillar,
        fuel_phase = EXCLUDED.fuel_phase,
        hardware = EXCLUDED.hardware,
        objective = EXCLUDED.objective,
        hypothesis = EXCLUDED.hypothesis,
        approach = EXCLUDED.approach,
        impacts = EXCLUDED.impacts,
        doi = EXCLUDED.doi,
        start_date = EXCLUDED.start_date,
        end_date = EXCLUDED.end_date,
        raw = EXCLUDED.raw
""")


def main() -> None:
    files = sorted(DATA_DIR.glob("PSI-*/investigation.json"))
    if not files:
        raise FileNotFoundError(f"No investigation.json files found in {DATA_DIR}")

    records = [investigation_values(path) for path in files]
    with engine.begin() as connection:
        connection.execute(UPSERT_INVESTIGATION, records)

    print(f"Upserted {len(records)} investigations into psi_flame.psi_dashboard.investigations.")


if __name__ == "__main__":
    main()
