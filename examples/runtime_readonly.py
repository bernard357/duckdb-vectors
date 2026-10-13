"""Runtime use of a frozen, read-only index baked into the container image.

The store opens read-only (no experimental persistence flag needed, since no
HNSW index is created) and answers hybrid queries. Settings come from the
``DUCKDB_VECTORS_*`` environment variables set in the Dockerfile.
"""

from duckdb_vectors import Settings, VectorStore


def main() -> None:
    settings = Settings(read_only=True)
    store = VectorStore(settings=settings)
    try:
        results = store.query("how does the system handle retries?", top_k=5)
        for result in results:
            snippet = result.chunk.text[:80].replace("\n", " ")
            print(f"{result.score:.4f}  {result.chunk.chunk_id}  {snippet}")
    finally:
        store.close()


if __name__ == "__main__":
    main()
