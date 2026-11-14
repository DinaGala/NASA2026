"""Load data/processed/chunks.parquet into psi_dashboard.chunks.

Truncates, then reloads in one transaction, so it can be re-run safely.
"""

from pathlib import Path

import pandas as pd
from sqlalchemy import text

from src.database import engine

ROOT = Path(__file__).resolve().parents[1]
CHUNKS_PARQUET = ROOT / "data" / "processed" / "chunks.parquet"
COLUMNS = ("chunk_id", "psi_id", "doc_title", "doc_category", "page", "text", "embedding")


def to_vector(values) -> str:
    """pgvector's text input format: '[0.1,0.2,...]'."""
    return "[" + ",".join(f"{value:.7g}" for value in values) + "]"


def main() -> None:
    chunks = pd.read_parquet(CHUNKS_PARQUET)

    with engine.begin() as connection:
        connection.execute(text("TRUNCATE psi_dashboard.chunks"))
        cursor = connection.connection.driver_connection.cursor()
        with cursor.copy(f"COPY psi_dashboard.chunks ({', '.join(COLUMNS)}) FROM STDIN") as copy:
            for row in chunks.itertuples(index=False):
                copy.write_row(
                    (
                        row.chunk_id,
                        row.psi_id,
                        row.doc_title,
                        row.doc_category,
                        None if pd.isna(row.page) else int(row.page),
                        row.text,
                        to_vector(row.embedding),
                    )
                )
        loaded = connection.execute(text("SELECT count(*) FROM psi_dashboard.chunks")).scalar_one()

    print(f"Loaded {loaded} chunks (parquet has {len(chunks)}).")
    if loaded != len(chunks):
        raise SystemExit("Row count mismatch between parquet and psi_dashboard.chunks")


if __name__ == "__main__":
    main()
