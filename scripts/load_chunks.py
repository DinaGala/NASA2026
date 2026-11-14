"""Load data/processed/chunks.parquet into psi_dashboard.chunks, then rebuild the BM25 tables.

Truncates, then reloads in one transaction, so it can be re-run safely.
"""

from pathlib import Path

import pandas as pd
from sqlalchemy import text

from src.database import engine

ROOT = Path(__file__).resolve().parents[1]
CHUNKS_PARQUET = ROOT / "data" / "processed" / "chunks.parquet"
COLUMNS = ("chunk_id", "psi_id", "doc_title", "doc_category", "page", "text", "embedding")


# BM25 statistics from each chunk's words (the text_search column Postgres fills in).
# tf = how many times the word occurs in the chunk; length = all its word occurrences.
BM25_SQL = (
    """
    INSERT INTO psi_dashboard.chunk_terms (term, chunk_id, tf)
    SELECT word.lexeme, chunks.chunk_id, coalesce(array_length(word.positions, 1), 1)
    FROM psi_dashboard.chunks AS chunks, unnest(chunks.text_search) AS word
    """,
    """
    INSERT INTO psi_dashboard.chunk_lengths (chunk_id, psi_id, length)
    SELECT chunks.chunk_id, chunks.psi_id, coalesce(sum(terms.tf), 0)
    FROM psi_dashboard.chunks AS chunks
    LEFT JOIN psi_dashboard.chunk_terms AS terms USING (chunk_id)
    GROUP BY chunks.chunk_id, chunks.psi_id
    """,
    "ANALYZE psi_dashboard.chunk_terms",
    "ANALYZE psi_dashboard.chunk_lengths",
)


def to_vector(values) -> str:
    """pgvector's text input format: '[0.1,0.2,...]'."""
    return "[" + ",".join(f"{value:.7g}" for value in values) + "]"


def main() -> None:
    chunks = pd.read_parquet(CHUNKS_PARQUET)

    with engine.begin() as connection:
        connection.execute(
            text("TRUNCATE psi_dashboard.chunk_terms, psi_dashboard.chunk_lengths, psi_dashboard.chunks")
        )
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
        for statement in BM25_SQL:
            connection.execute(text(statement))
        terms = connection.execute(text("SELECT count(*) FROM psi_dashboard.chunk_terms")).scalar_one()

    print(f"Loaded {loaded} chunks (parquet has {len(chunks)}) and {terms} BM25 term rows.")
    if loaded != len(chunks):
        raise SystemExit("Row count mismatch between parquet and psi_dashboard.chunks")


if __name__ == "__main__":
    main()
