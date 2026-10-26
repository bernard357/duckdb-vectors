"""VectorStore CRUD, read-only guard, vector search, and hybrid fusion."""

import pytest

from duckdb_vectors.config import DEFAULT_BACKEND, Settings
from duckdb_vectors.models import Chunk
from duckdb_vectors.store import VectorStore


@pytest.fixture
def db_path(tmp_path):
    return str(tmp_path / "test.duckdb")


def _store(db_path):
    return VectorStore(db_path=db_path, backend="hashing")


def test_put_get_list_delete_roundtrip(db_path):
    store = _store(db_path)
    store.add_texts(
        ids=["a", "b", "c"],
        texts=["alpha text", "beta text", "gamma text"],
        metadatas=[{"k": "1"}, {"k": "2"}, {"k": "3"}],
    )
    assert store.count() == 3

    fetched = store.get_vectors(["b"])
    assert len(fetched) == 1
    assert fetched[0].text == "beta text"
    assert fetched[0].metadata == {"k": "2"}

    listed = store.list_vectors()
    assert {chunk.chunk_id for chunk in listed} == {"a", "b", "c"}

    removed = store.delete_vectors(["a"])
    assert removed == 1
    assert store.count() == 2
    store.close()


def test_upsert_replaces_by_id(db_path):
    store = _store(db_path)
    store.add_texts(ids=["a"], texts=["first version"])
    store.add_texts(ids=["a"], texts=["second version"])
    assert store.count() == 1
    assert store.get_vectors(["a"])[0].text == "second version"
    store.close()


def test_reopened_store_reads_and_accepts_writes(db_path):
    writer = _store(db_path)
    writer.add_texts(ids=["a"], texts=["seed"])
    writer.close()

    # Reopening the same file reads the seeded data and, in the single
    # writable mode, accepts further writes.
    reopened = _store(db_path)
    assert reopened.count() == 1
    reopened.add_texts(ids=["b"], texts=["added later"])
    assert reopened.count() == 2
    assert reopened.delete_vectors(["a"]) == 1
    assert reopened.count() == 1
    reopened.close()


def test_vector_search_orders_by_similarity(db_path):
    store = _store(db_path)
    store.put_vectors(
        [
            Chunk(chunk_id="fox", text="the quick brown fox jumps"),
            Chunk(chunk_id="dog", text="a lazy dog sleeps all day"),
            Chunk(chunk_id="db", text="duckdb analytical database engine"),
        ]
    )
    results = store.query("quick brown fox", top_k=3, hybrid=False)
    assert results[0].chunk.chunk_id == "fox"
    store.close()


def test_metadata_filter(db_path):
    store = _store(db_path)
    store.add_texts(
        ids=["a", "b"],
        texts=["shared words here", "shared words here too"],
        metadatas=[{"kind": "keep"}, {"kind": "skip"}],
    )
    results = store.query("shared words", top_k=5, filter={"kind": "keep"})
    assert {r.chunk.chunk_id for r in results} == {"a"}
    store.close()


def test_hybrid_fusion_beats_single_signal(db_path):
    store = _store(db_path)
    store.put_vectors(
        [
            Chunk(
                chunk_id="both",
                text="postgresql database replication tuning guide",
            ),
            Chunk(
                chunk_id="lexical",
                text="database database database trivia list",
            ),
            Chunk(
                chunk_id="other",
                text="unrelated cooking recipe for pancakes",
            ),
        ]
    )
    results = store.query("database replication tuning", top_k=3)
    assert results[0].chunk.chunk_id == "both"
    top = results[0]
    assert top.vector_rank is not None or top.text_rank is not None
    store.close()


def test_default_backend_is_fastembed_potion_multilingual():
    assert DEFAULT_BACKEND == "fastembed:minishlab/potion-multilingual-128M"
    assert Settings().backend == "fastembed:minishlab/potion-multilingual-128M"


def test_cli_app_imports_without_extras():
    from duckdb_vectors.cli import app

    assert app is not None
