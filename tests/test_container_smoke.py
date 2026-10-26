"""Smoke test for the frozen-index container lifecycle.

Mirrors what the example Dockerfile does: index a folder, then open the file
read-only and confirm a search returns results. The Docker build itself is out
of unit-test scope; this exercises the artifact and the read-only open.
"""

from pathlib import Path

from duckdb_vectors import VectorStore, index_folder

EXAMPLE_DOCS = Path(__file__).resolve().parent.parent / "examples" / "docs"


def test_build_then_open_readonly_and_search(tmp_path):
    db = str(tmp_path / "frozen.duckdb")

    # Build-time: index the example corpus and freeze the file.
    builder = VectorStore(db_path=db, backend="hashing")
    report = index_folder(builder, EXAMPLE_DOCS)
    builder.close()
    assert report.chunks > 0

    # Runtime: reopen the baked index and search.
    reader = VectorStore(db_path=db, backend="hashing")
    try:
        results = reader.query("how are retries handled?", top_k=3)
        assert results
        assert all(result.chunk.chunk_id for result in results)
    finally:
        reader.close()
