"""VectorStore CRUD, read-only guard, vector search, and hybrid fusion."""

import os
import subprocess
import sys
import time

import duckdb
import pytest

from duckdb_vectors.config import DEFAULT_BACKEND, Settings
from duckdb_vectors.models import Chunk
from duckdb_vectors.store import DatabaseLockedError, VectorStore


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


# Cross-process locking. DuckDB shares one database instance per file inside a
# process, so lock conflicts only appear between separate processes: these tests
# hold or contend for the file from child interpreters.

_HOLD_LOCK = """
import sys, time, duckdb
con = duckdb.connect(sys.argv[1])
print("ready", flush=True)
time.sleep(float(sys.argv[2]))
con.close()
"""

_QUERY_LOOP = """
import sys
from duckdb_vectors.store import VectorStore
ok = 0
for _ in range(int(sys.argv[2])):
    store = VectorStore(db_path=sys.argv[1], backend="hashing")
    try:
        store.query("analytical database", top_k=1)
        ok += 1
    finally:
        store.close()
print(ok)
"""


def _hold_lock(db_path, seconds):
    """Start a child process holding the database file; return once it has it."""
    child = subprocess.Popen(
        [sys.executable, "-c", _HOLD_LOCK, db_path, str(seconds)],
        stdout=subprocess.PIPE,
        text=True,
    )
    assert child.stdout is not None
    assert child.stdout.readline().strip() == "ready"
    return child


def _seed(db_path):
    store = _store(db_path)
    store.add_texts(ids=["db"], texts=["duckdb analytical database engine"])
    store.close()


def test_store_connects_lazily(db_path):
    store = _store(db_path)
    assert store._connection is None
    assert not os.path.exists(db_path)
    assert store.count() == 0
    assert os.path.exists(db_path)
    store.close()
    assert store._connection is None


@pytest.mark.slow
def test_open_waits_for_a_lock_held_by_another_process(db_path):
    _seed(db_path)
    child = _hold_lock(db_path, 1.0)
    try:
        started = time.monotonic()
        store = _store(db_path)
        assert store.count() == 1
        store.close()
        assert time.monotonic() - started >= 0.5
    finally:
        child.wait(timeout=10)


@pytest.mark.slow
def test_open_gives_up_after_the_lock_timeout(db_path):
    _seed(db_path)
    child = _hold_lock(db_path, 5.0)
    try:
        store = VectorStore(
            settings=Settings(db_path=db_path, backend="hashing", lock_timeout=0.3)
        )
        started = time.monotonic()
        with pytest.raises(DatabaseLockedError, match="locked by another process"):
            store.count()
        assert time.monotonic() - started < 3.0
    finally:
        child.kill()
        child.wait(timeout=10)


def test_other_io_errors_are_not_retried(tmp_path):
    missing = str(tmp_path / "no-such-dir" / "x.duckdb")
    store = VectorStore(
        settings=Settings(db_path=missing, backend="hashing", lock_timeout=10.0)
    )
    started = time.monotonic()
    with pytest.raises(duckdb.IOException, match="No such file"):
        store.count()
    assert time.monotonic() - started < 2.0


@pytest.mark.slow
def test_two_processes_query_one_file_concurrently(db_path):
    _seed(db_path)
    rounds = "15"
    children = [
        subprocess.Popen(
            [sys.executable, "-c", _QUERY_LOOP, db_path, rounds],
            stdout=subprocess.PIPE,
            text=True,
        )
        for _ in range(2)
    ]
    outputs = [child.communicate(timeout=120)[0].strip() for child in children]
    assert [child.returncode for child in children] == [0, 0]
    assert outputs == [rounds, rounds]
