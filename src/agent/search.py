"""Semantic search over the PSI papers stored in psi_dashboard.chunks."""

from dataclasses import dataclass

from sqlalchemy import bindparam, text
from sqlalchemy.dialects.postgresql import ARRAY, TEXT

from src.agent.embeddings import embed_query
from src.database import engine


@dataclass(frozen=True)
class PaperHit:
    psi_id: str
    doc_title: str
    page: int | None
    text: str
    score: float  # cosine similarity, 1 = identical


SEARCH_SQL = text(
    """
    SELECT psi_id, doc_title, page, text,
           1 - (embedding <=> CAST(:query_vector AS vector)) AS score
    FROM psi_dashboard.chunks
    WHERE CAST(:psi_ids AS text[]) IS NULL OR psi_id = ANY(:psi_ids)
    ORDER BY embedding <=> CAST(:query_vector AS vector)
    LIMIT :k
    """
).bindparams(bindparam("psi_ids", type_=ARRAY(TEXT)))


def search_papers(query: str, psi_ids: list[str] | None = None, k: int = 5) -> list[PaperHit]:
    """Return the k chunks closest to the query, optionally only from some investigations."""
    query_vector = "[" + ",".join(map(str, embed_query(query))) + "]"
    with engine.connect() as connection:
        rows = connection.execute(
            SEARCH_SQL, {"query_vector": query_vector, "psi_ids": psi_ids, "k": k}
        )
        return [PaperHit(**row._mapping) for row in rows]
