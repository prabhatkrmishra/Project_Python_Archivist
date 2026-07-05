"""Text vectorization using HashingVectorizer.

Provides stateless TF-IDF vectorization using scikit-learn's HashingVectorizer.
No model training required — vectors are computed on-the-fly.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from qdrant_client import models

if TYPE_CHECKING:
    from qdrant_client import QdrantClient


# Module-level cache for HashingVectorizer instances
_vectorizer_cache: dict[tuple[int, str], object] = {}


def csr_to_qdrant_sparse(csr_row) -> models.SparseVector:
    """Convert one row of a scipy sparse CSR matrix to Qdrant's format.

    Args:
        csr_row: Single row from a CSR matrix.

    Returns:
        Qdrant SparseVector with indices and values.
    """
    return models.SparseVector(
        indices=csr_row.indices.tolist(),
        values=csr_row.data.tolist(),
    )


def hashing_vectorize(
    texts: list[str],
    n_features: int = 1_048_576,
    norm: str = "l2",
) -> models.SparseVector:
    """Vectorize text using HashingVectorizer (stateless TF-IDF approx).

    Uses a cached HashingVectorizer instance for performance.
    The vectorizer is stateless — no fitting required.

    Args:
        texts: List of text strings to vectorize.
        n_features: Number of feature dimensions (default 2^20).
        norm: Normalization type (l2 recommended).

    Returns:
        Qdrant SparseVector representation.
    """
    from sklearn.feature_extraction.text import HashingVectorizer

    key = (n_features, norm)
    if key not in _vectorizer_cache:
        _vectorizer_cache[key] = HashingVectorizer(
            n_features=n_features,
            alternate_sign=False,
            norm=norm,
            lowercase=True,
            strip_accents="unicode",
            token_pattern=r"(?u)\b\w\w+\b",
            stop_words="english",
        )
    vectorizer = _vectorizer_cache[key]
    matrix = vectorizer.transform(texts)
    return csr_to_qdrant_sparse(matrix[0])


def bm25_vectorize(text: str) -> models.Document:
    """Vectorize text using Qdrant's native BM25 sparse vector model.

    Requires Qdrant server v1.7+ and sends the raw text for server-side
    tokenization and BM25 scoring.

    Args:
        text: Text to vectorize.

    Returns:
        Qdrant Document for server-side BM25 processing.
    """
    return models.Document(text=text, model="Qdrant/bm25")


def vectorize(
    text: str,
    n_features: int = 1_048_576,
    use_bm25: bool = False,
) -> models.SparseVector | models.Document:
    """Dispatch to the chosen vectorization backend.

    Args:
        text: Text to vectorize.
        n_features: Feature dimensions (for HashingVectorizer).
        use_bm25: If True, use Qdrant BM25; otherwise use HashingVectorizer.

    Returns:
        SparseVector or Document depending on backend.
    """
    if use_bm25:
        return bm25_vectorize(text)
    return hashing_vectorize([text], n_features=n_features)
