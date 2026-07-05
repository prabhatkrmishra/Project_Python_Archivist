"""Benchmark: estimate ingestion + search latency for N documents.

Usage:
  python scripts/benchmark.py --docs 10000
  python scripts/benchmark.py --docs 1000000 --workers 8
"""
from __future__ import annotations

import argparse
import time
from pathlib import Path
import tempfile

from archivist.config import get_settings
from archivist.ingestion.extractors import iter_files
from archivist.ingestion.pipeline import ingest_file
from archivist.ingestion.tracker import Tracker
from archivist.search.qdrant_client import search
from archivist.vectorizer.hashing_tfidf import vectorize
from qdrant_client import QdrantClient


def generate_fake_docs(count: int, dest: Path) -> Path:
    dest.mkdir(parents=True, exist_ok=True)
    for i in range(count):
        (dest / f"doc_{i:06d}.txt").write_text(f"Document {i} about topic {i % 100}. " * 20)
    return dest


def run_benchmark(n_docs: int, workers: int) -> None:
    settings = get_settings()
    client = QdrantClient(url=str(settings.qdrant_url), api_key=settings.qdrant_api_key)
    from archivist.search.qdrant_client import ensure_collection
    ensure_collection(client, settings.qdrant_collection)
    client.close()

    tracker = Tracker(settings.tracker_db)

    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        print(f"Generating {n_docs} fake documents...")
        generate_fake_docs(n_docs, root)

        files = list(iter_files(root))
        print(f"Ingesting {len(files)} files with {workers} workers...")
        t0 = time.time()
        for i, fp in enumerate(files, 1):
            ingest_file(fp, tracker)
            if i % 1000 == 0:
                elapsed = time.time() - t0
                rate = i / elapsed
                print(f"  [{i}/{len(files)}] {rate:.1f} docs/sec")
        ingest_time = time.time() - t0
        rate = len(files) / ingest_time
        print(f"Ingestion complete: {len(files)} docs in {ingest_time:.1f}s ({rate:.1f} docs/sec)")

        client = QdrantClient(url=str(settings.qdrant_url), api_key=settings.qdrant_api_key)
        q_vec = vectorize("topic about finance and budget")
        t0 = time.time()
        hits = search(client, settings.qdrant_collection, q_vec, limit=10)
        search_time = (time.time() - t0) * 1000
        client.close()
        print(f"Search latency: {search_time:.1f}ms (top-10 returned)")
        print(f"Top score: {hits[0].score:.4f}" if hits else "No results")

    tracker.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--docs", type=int, default=1000, help="Number of fake docs to generate")
    parser.add_argument("--workers", type=int, default=4)
    args = parser.parse_args()
    run_benchmark(args.docs, args.workers)
