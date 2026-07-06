"""Text vectorization using HashingVectorizer.

Provides stateless TF-IDF vectorization using scikit-learn's HashingVectorizer.
No model training required — vectors are computed on-the-fly.
"""

from __future__ import annotations

from typing import Any


# Module-level cache for HashingVectorizer instances
_vectorizer_cache: dict[tuple[int, str], Any] = {}


def hashing_vectorize(
    texts: list[str],
    n_features: int = 1_048_576,
    norm: str = "l2",
) -> object:
    """Vectorize text using HashingVectorizer (stateless TF-IDF approx).

    Uses a cached HashingVectorizer instance for performance.
    The vectorizer is stateless — no fitting required.

    Args:
        texts: List of text strings to vectorize.
        n_features: Number of feature dimensions (default 2^20).
        norm: Normalization type (l2 recommended).

    Returns:
        Sparse matrix representation.
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
    return matrix[0]
