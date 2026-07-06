import numpy as np

from archivist.vectorizer.hashing_tfidf import hashing_vectorize


def test_hashing_vectorize_returns_sparse_matrix():
    sv = hashing_vectorize(["hello world test"], n_features=1024)
    assert sv is not None
    assert hasattr(sv, "indices") or hasattr(sv, "nnz")
