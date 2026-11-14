from sqlalchemy import text
from src.database import engine

def main():
    with engine.connect() as connection:
        # Create schema if it doesn't exist
        connection.execute(text("CREATE SCHEMA IF NOT EXISTS psi_dashboard"))
        
        # Create investigations table
        connection.execute(text("""
            CREATE TABLE IF NOT EXISTS psi_dashboard.investigations (
                id SERIAL PRIMARY KEY,
                psi_id TEXT UNIQUE NOT NULL,
                name TEXT NOT NULL,
                experiment_family TEXT,
                source_url TEXT,
                metadata_json JSONB
            )
        """))
        
        # Create source_files table
        connection.execute(text("""
            CREATE TABLE IF NOT EXISTS psi_dashboard.source_files (
                id SERIAL PRIMARY KEY,
                investigation_id INTEGER REFERENCES psi_dashboard.investigations(id),
                relative_path TEXT NOT NULL,
                file_type TEXT,
                imported_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                import_status TEXT,
                notes TEXT,
                UNIQUE(investigation_id, relative_path)
            )
        """))

        # Create runs table
        connection.execute(text("""
            CREATE TABLE IF NOT EXISTS psi_dashboard.runs (
                id SERIAL PRIMARY KEY,
                investigation_id INTEGER REFERENCES psi_dashboard.investigations(id),
                source_run_id TEXT,
                date TEXT,
                gmt TEXT,
                fuel TEXT,
                geometry TEXT,
                material_details TEXT,
                flow_direction TEXT,
                source_file TEXT,
                UNIQUE(investigation_id, source_run_id, source_file)
            )
        """))
        
        # Create run_values table
        connection.execute(text("""
            CREATE TABLE IF NOT EXISTS psi_dashboard.run_values (
                id SERIAL PRIMARY KEY,
                run_id INTEGER REFERENCES psi_dashboard.runs(id),
                variable TEXT NOT NULL,
                numeric_value DOUBLE PRECISION,
                text_value TEXT,
                unit TEXT,
                phase TEXT,
                source_column TEXT,
                source_file TEXT
            )
        """))
        
        # Create design_ranges table
        connection.execute(text("""
            CREATE TABLE IF NOT EXISTS psi_dashboard.design_ranges (
                id SERIAL PRIMARY KEY,
                investigation_id INTEGER REFERENCES psi_dashboard.investigations(id),
                fuel TEXT,
                parameter TEXT,
                range_text TEXT,
                unit TEXT,
                source_file TEXT
            )
        """))
        
        connection.commit()
        print("Schema applied successfully in 'psi_dashboard' schema.")

if __name__ == "__main__":
    main()
