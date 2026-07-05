import numpy as np

from archivist.vectorizer.hashing_tfidf import csr_to_qdrant_sparse, hashing_vectorize
from qdrant_client import models


def test_csr_to_qdrant_sparse():
    class FakeCSR:
        indices = np.array([0, 5, 10], dtype=np.int64)
        data = np.array([1.0, 0.5, 0.3])

    sv = csr_to_qdrant_sparse(FakeCSR())
    assert isinstance(sv, models.SparseVector)
    assert sv.indices == [0, 5, 10]
    assert sv.values == [1.0, 0.5, 0.3]


def test_hashing_vectorize_returns_sparse_vector():
    sv = hashing_vectorize(["hello world test"], n_features=1024)
    assert isinstance(sv, models.SparseVector)
    assert len(sv.indices) > 0
