"""Create the agreed first-stage PSI dashboard schema."""

from sqlalchemy import text

from src.database import engine


SCHEMA_SQL = (
    "CREATE SCHEMA IF NOT EXISTS psi_dashboard",
    """
    CREATE TABLE IF NOT EXISTS psi_dashboard.investigations (
        psi_id TEXT PRIMARY KEY,
        acronym TEXT,
        title TEXT NOT NULL,
        pillar TEXT,
        fuel_phase TEXT,
        hardware TEXT,
        objective TEXT,
        hypothesis TEXT,
        approach TEXT,
        impacts TEXT,
        doi TEXT,
        start_date DATE,
        end_date DATE,
        raw JSONB NOT NULL DEFAULT '{}'::jsonb
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS psi_dashboard.scenarios (
        test_id TEXT PRIMARY KEY,
        psi_id TEXT NOT NULL REFERENCES psi_dashboard.investigations(psi_id),
        source_file TEXT,
        run_label TEXT,
        granularity TEXT CHECK (granularity IS NULL OR granularity IN ('test', 'range')),
        flame_config TEXT CHECK (
            flame_config IS NULL OR flame_config IN
            ('droplet', 'gas_normal', 'gas_inverse', 'solid', 'smoke')
        ),
        fuel TEXT,
        fuel_phase TEXT,
        material TEXT,
        geometry TEXT,
        size_mm DOUBLE PRECISION,
        o2_frac DOUBLE PRECISION CHECK (o2_frac IS NULL OR o2_frac BETWEEN 0 AND 1),
        o2_is_ambient BOOLEAN,
        pressure_atm DOUBLE PRECISION,
        diluent TEXT,
        diluent_frac DOUBLE PRECISION CHECK (
            diluent_frac IS NULL OR diluent_frac BETWEEN 0 AND 1
        ),
        flow_cm_s DOUBLE PRECISION,
        gravity TEXT,
        outcome TEXT CHECK (
            outcome IS NULL OR outcome IN
            ('extinction', 'disruption', 'completion', 'ignited',
             'no_ignition', 'blowoff', 'unknown')
        ),
        outcome_source TEXT,
        notes TEXT,
        raw JSONB NOT NULL DEFAULT '{}'::jsonb
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS psi_dashboard.measurements (
        measurement_id BIGSERIAL PRIMARY KEY,
        test_id TEXT NOT NULL REFERENCES psi_dashboard.scenarios(test_id),
        variable TEXT NOT NULL,
        value DOUBLE PRECISION,
        unit TEXT
    )
    """,
)


def main() -> None:
    with engine.begin() as connection:
        for statement in SCHEMA_SQL:
            connection.execute(text(statement))

    print("Schema applied successfully to psi_flame.psi_dashboard.")


if __name__ == "__main__":
    main()
