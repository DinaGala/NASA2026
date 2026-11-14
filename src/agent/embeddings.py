"""The bge-small embedding model, shared by the chunker and search_papers."""

from functools import cache

from sentence_transformers import SentenceTransformer

MODEL_NAME = "BAAI/bge-small-en-v1.5"
DIMENSIONS = 384
# bge expects this prefix in front of queries, and nothing in front of passages.
QUERY_PREFIX = "Represent this sentence for searching relevant passages: "


@cache
def get_model() -> SentenceTransformer:
    """Load the model once per process."""
    return SentenceTransformer(MODEL_NAME)


def embed_passages(texts: list[str], batch_size: int = 64) -> list[list[float]]:
    vectors = get_model().encode(
        texts, batch_size=batch_size, normalize_embeddings=True, show_progress_bar=True
    )
    return vectors.tolist()


def embed_query(query: str) -> list[float]:
    return get_model().encode(QUERY_PREFIX + query, normalize_embeddings=True).tolist()
