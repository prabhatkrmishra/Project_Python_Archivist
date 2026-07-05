"""Qdrant vector database client.

Provides vector search backend using Qdrant for large-scale deployments.
Supports sparse vectors with HNSW indexing for fast approximate nearest neighbor search.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from qdrant_client import models

if TYPE_CHECKING:
    from qdrant_client import QdrantClient


_COLLECTION_NAME = "archivist_docs"


def ensure_collection(
    client: QdrantClient,
    collection_name: str,
    on_disk: bool = False,
) -> None:
    """Create the collection and payload indexes if they don't exist.

    Args:
        client: Qdrant client instance.
        collection_name: Name of the collection to create.
        on_disk: If True, store index on disk (for large datasets).
    """
    from qdrant_client import QdrantClient

    if not isinstance(client, QdrantClient):
        raise TypeError("Expected QdrantClient instance")

    existing = set(c.name for c in client.get_collections().collections)
    if collection_name not in existing:
        client.create_collection(
            collection_name=collection_name,
            vectors_config={},
            sparse_vectors_config={
                "text": models.SparseVectorParams(
                    index=models.SparseIndexParams(on_disk=on_disk)
                )
            },
        )

    _ensure_payload_index(client, collection_name, "file_hash", "keyword")
    _ensure_payload_index(client, collection_name, "filepath", "keyword")
    _ensure_payload_index(client, collection_name, "content", "text")


def _ensure_payload_index(
    client: QdrantClient,
    collection_name: str,
    field_name: str,
    field_type: str,
) -> None:
    """Create a payload index if it doesn't exist.

    Args:
        client: Qdrant client instance.
        collection_name: Collection name.
        field_name: Payload field to index.
        field_type: Index type (keyword, text, etc.).
    """
    from qdrant_client import QdrantClient

    existing = set(
        idx.field_name
        for idx in client.get_collection(collection_name).payload_schema
        if hasattr(idx, "field_name") and idx.field_name
    )
    if field_name not in existing:
        schema_cls = getattr(models, f"{field_type.capitalize()}IndexParams", None)
        if schema_cls is None:
            schema_cls = models.TextIndexParams
        client.create_payload_index(
            collection_name=collection_name,
            field_name=field_name,
            field_schema=schema_cls(type=field_type),
        )


def build_point(
    sparse_vector: models.SparseVector | models.Document,
    payload: dict,
    point_id: str | None = None,
) -> models.PointStruct:
    """Build a Qdrant PointStruct for upsert.

    Args:
        sparse_vector: Sparse vector or BM25 Document.
        payload: Document metadata.
        point_id: Optional UUID string.

    Returns:
        Configured PointStruct.
    """
    if isinstance(sparse_vector, models.Document):
        vector = sparse_vector
    else:
        vector = {"text": sparse_vector}
    return models.PointStruct(
        id=point_id or str(uuid.uuid4()),
        vector=vector,
        payload=payload,
    )


def upsert_points(
    client: QdrantClient,
    collection_name: str,
    points: list[models.PointStruct],
) -> None:
    """Upsert a batch of points to Qdrant.

    Args:
        client: Qdrant client instance.
        collection_name: Target collection.
        points: List of PointStruct to upsert.
    """
    client.upsert(
        collection_name=collection_name,
        points=points,
        wait=True,
    )


def search(
    client: QdrantClient,
    collection_name: str,
    query_vector: models.SparseVector | models.Document,
    limit: int = 10,
    query_filter: models.Filter | None = None,
    ef: int = 100,
) -> list[models.ScoredPoint]:
    """Search Qdrant collection and return scored hits.

    Args:
        client: Qdrant client instance.
        collection_name: Collection to search.
        query_vector: Query vector.
        limit: Maximum results.
        query_filter: Optional filter.
        ef: HNSW ef parameter.

    Returns:
        List of ScoredPoint results.
    """
    results = client.query_points(
        collection_name=collection_name,
        query=query_vector,
        using="text",
        limit=limit,
        query_filter=query_filter,
        search_params=models.SearchParams(hnsw_ef=ef),
    )
    return results.points


def delete_points(
    client: QdrantClient,
    collection_name: str,
    point_ids: list[str],
) -> None:
    """Delete points by ID.

    Args:
        client: Qdrant client instance.
        collection_name: Target collection.
        point_ids: List of point IDs to delete.
    """
    client.delete(
        collection_name=collection_name,
        points_selector=models.PointIdsList(points=point_ids),
    )


def get_stats(client: QdrantClient, collection_name: str) -> dict:
    """Get collection statistics.

    Args:
        client: Qdrant client instance.
        collection_name: Collection name.

    Returns:
        Dictionary with points_count, status, vectors_count.
    """
    info = client.get_collection(collection_name)
    return {
        "points_count": info.points_count,
        "status": info.status.value if hasattr(info.status, "value") else str(info.status),
        "vectors_count": getattr(info, "vectors_count", info.points_count),
    }
