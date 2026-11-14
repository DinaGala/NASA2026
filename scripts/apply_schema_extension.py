from sqlalchemy import text
from src.database import engine

def main():
    with engine.connect() as connection:
        print("Applying additive schema extensions...")

        # 1. sensor_measurements table
        connection.execute(text("""
            CREATE TABLE IF NOT EXISTS psi_dashboard.sensor_measurements (
                id SERIAL PRIMARY KEY,
                run_id INTEGER REFERENCES psi_dashboard.runs(id),
                source_file_id INTEGER REFERENCES psi_dashboard.source_files(id),
                time_seconds DOUBLE PRECISION,
                timestamp TIMESTAMP,
                sensor TEXT,
                variable TEXT NOT NULL,
                numeric_value DOUBLE PRECISION,
                text_value TEXT,
                unit TEXT,
                source_column TEXT,
                source_row_number INTEGER,
                quality_note TEXT,
                quality_flag TEXT,
                UNIQUE(run_id, sensor, variable, time_seconds, source_row_number)
            )
        """))
        
        # Indexes for sensor_measurements
        connection.execute(text("CREATE INDEX IF NOT EXISTS idx_sensor_run ON psi_dashboard.sensor_measurements(run_id)"))
        connection.execute(text("CREATE INDEX IF NOT EXISTS idx_sensor_file ON psi_dashboard.sensor_measurements(source_file_id)"))
        connection.execute(text("CREATE INDEX IF NOT EXISTS idx_sensor_var ON psi_dashboard.sensor_measurements(variable)"))
        connection.execute(text("CREATE INDEX IF NOT EXISTS idx_sensor_time ON psi_dashboard.sensor_measurements(time_seconds)"))

        # 2. run_events table
        connection.execute(text("""
            CREATE TABLE IF NOT EXISTS psi_dashboard.run_events (
                id SERIAL PRIMARY KEY,
                run_id INTEGER REFERENCES psi_dashboard.runs(id),
                source_file_id INTEGER REFERENCES psi_dashboard.source_files(id),
                event_name TEXT NOT NULL,
                time_seconds DOUBLE PRECISION,
                event_time_text TEXT,
                notes TEXT
            )
        """))
        
        # 3. calibration_points table
        connection.execute(text("""
            CREATE TABLE IF NOT EXISTS psi_dashboard.calibration_points (
                id SERIAL PRIMARY KEY,
                investigation_id INTEGER REFERENCES psi_dashboard.investigations(id),
                source_file_id INTEGER REFERENCES psi_dashboard.source_files(id),
                calibration_type TEXT,
                calibration_context TEXT,
                input_value DOUBLE PRECISION,
                input_unit TEXT,
                output_value DOUBLE PRECISION,
                output_unit TEXT,
                notes TEXT
            )
        """))

        # Add index for source_run_id in runs table if it helps performance
        connection.execute(text("CREATE INDEX IF NOT EXISTS idx_runs_source_run_id ON psi_dashboard.runs(source_run_id)"))

        connection.commit()
        print("Schema extensions applied successfully.")

if __name__ == "__main__":
    main()
