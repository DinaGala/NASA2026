"""Search over the PSI papers stored in psi_dashboard.chunks, in two stages.

1. Shortlist (one SQL query, fast): two rankings of the same chunks, merged
   - semantic: cosine distance between the question's and the chunk's embeddings (meaning);
   - keyword: Postgres full-text rank on the chunk's words (exact terms such as "CO2");
   fused with reciprocal rank fusion (RRF): each list gives a chunk 1 / (60 + rank), so a
   chunk near the top of either list, or fairly high in both, makes the shortlist.
2. Re-rank (precise, ~1 s): a cross-encoder reads the question and each shortlisted chunk
   together and scores how well the chunk answers it. The best k are returned.
"""

import math
from dataclasses import dataclass
from functools import cache

from sentence_transformers import CrossEncoder
from sqlalchemy import bindparam, text
from sqlalchemy.dialects.postgresql import ARRAY, TEXT

from src.agent.embeddings import embed_query
from src.database import engine

# How many chunks each ranking contributes before fusion: the shortlist holds up to twice this.
CANDIDATES = 50
# RRF constant: 60 is the usual value; larger flattens the gap between ranks.
RRF_K = 60
# Small and fast (~1 s for 100 chunks on a laptop CPU). bge-reranker-base scored the same on
# tests/test_search.py but took ~12 s.
RERANKER_NAME = "cross-encoder/ms-marco-MiniLM-L-6-v2"


@dataclass(frozen=True)
class PaperHit:
    psi_id: str
    doc_title: str
    page: int | None
    text: str
    score: float  # re-ranker relevance, 0 to 1: higher is better


SEARCH_SQL = text(
    """
    WITH semantic AS (
        SELECT chunk_id,
               row_number() OVER (ORDER BY embedding <=> CAST(:query_vector AS vector)) AS rank
        FROM psi_dashboard.chunks
        WHERE CAST(:psi_ids AS text[]) IS NULL OR psi_id = ANY(:psi_ids)
        ORDER BY embedding <=> CAST(:query_vector AS vector)
        LIMIT :candidates
    ),
    keyword AS (
        -- Any of the question's words may match (OR), ranked by how many match and how close.
        SELECT chunk_id,
               row_number() OVER (ORDER BY ts_rank_cd(text_search, words) DESC) AS rank
        FROM psi_dashboard.chunks,
             CAST(replace(CAST(plainto_tsquery('english', :query) AS text), ' & ', ' | ')
                  AS tsquery) AS words
        WHERE text_search @@ words
          AND (CAST(:psi_ids AS text[]) IS NULL OR psi_id = ANY(:psi_ids))
        ORDER BY ts_rank_cd(text_search, words) DESC
        LIMIT :candidates
    ),
    fused AS (
        SELECT chunk_id, sum(1.0 / (:rrf_k + rank)) AS score
        FROM (SELECT * FROM semantic UNION ALL SELECT * FROM keyword) AS ranked
        GROUP BY chunk_id
    )
    -- No dedupe of identical text: a paper filed under two studies belongs to both,
    -- and keeping one copy would hide a study.
    SELECT chunks.psi_id, chunks.doc_title, chunks.page, chunks.text, fused.score
    FROM fused JOIN psi_dashboard.chunks AS chunks USING (chunk_id)
    ORDER BY fused.score DESC
    LIMIT :k
    """
).bindparams(bindparam("psi_ids", type_=ARRAY(TEXT)))


@cache
def get_reranker() -> CrossEncoder:
    """Load the re-ranker once per process."""
    return CrossEncoder(RERANKER_NAME)


def search_papers(query: str, psi_ids: list[str] | None = None, k: int = 5) -> list[PaperHit]:
    """Return the k best chunks for the query, optionally only from some investigations."""
    query_vector = "[" + ",".join(map(str, embed_query(query))) + "]"
    with engine.connect() as connection:
        shortlist = connection.execute(
            SEARCH_SQL,
            {
                "query": query,
                "query_vector": query_vector,
                "psi_ids": psi_ids,
                "k": 2 * CANDIDATES,
                "candidates": CANDIDATES,
                "rrf_k": RRF_K,
            },
        ).all()
    if not shortlist:
        return []
    scores = get_reranker().predict([(query, row.text) for row in shortlist])
    ranked = sorted(zip(scores, shortlist), key=lambda pair: pair[0], reverse=True)[:k]
    return [
        # The model returns unbounded logits; the sigmoid maps them to 0..1 without reordering.
        PaperHit(row.psi_id, row.doc_title, row.page, row.text, 1 / (1 + math.exp(-score)))
        for score, row in ranked
    ]
