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


# Lazy keyword indexing: writes mark the full-text index stale in the file, and
# every keyword search checks that marker and rebuilds first when needed.


def _count_rebuilds(monkeypatch, delay: float = 0.0) -> list[int]:
    """Count full-text rebuilds across every store in this process."""
    calls = [0]
    original = VectorStore._rebuild_fts

    def counting(self):
        calls[0] += 1
        if delay:
            time.sleep(delay)
        original(self)

    monkeypatch.setattr(VectorStore, "_rebuild_fts", counting)
    return calls


def _leave_stale(db_path, ids, texts):
    """Write rows and release the file without rebuilding, like a crash."""
    store = _store(db_path)
    store.add_texts(ids=ids, texts=texts)
    store._keyword_dirty = False  # skip close()'s rebuild
    store.close()


def _text_rank(store, query, chunk_id):
    for result in store.query(query, top_k=10):
        if result.chunk.chunk_id == chunk_id:
            return result.text_rank
    return None


def test_many_small_writes_cost_one_rebuild(db_path, monkeypatch):
    rebuilds = _count_rebuilds(monkeypatch)
    store = _store(db_path)
    # Letter-only tokens: the full-text tokenizer drops digits.
    tokens = ["kappa", "lambda", "sigma", "theta", "upsilon"]
    for batch, token in enumerate(tokens):
        store.add_texts(ids=[f"c{batch}"], texts=[f"{token} words"])
    assert rebuilds[0] == 0
    assert _text_rank(store, "upsilon", "c4") == 1
    assert rebuilds[0] == 1
    store.close()
    assert rebuilds[0] == 1  # nothing written since the search rebuilt


def test_close_rebuilds_after_writes(db_path, monkeypatch):
    rebuilds = _count_rebuilds(monkeypatch)
    store = _store(db_path)
    store.add_texts(ids=["a"], texts=["alpha"])
    store.close()
    assert rebuilds[0] == 1
    reader = _store(db_path)
    assert _text_rank(reader, "alpha", "a") == 1
    reader.close()
    assert rebuilds[0] == 1  # the reader found the index current


def test_token_written_after_last_rebuild_is_found(db_path):
    store = _store(db_path)
    store.add_texts(ids=["a"], texts=["alpha words"])
    store.query("alpha", top_k=1)  # builds the index
    store.add_texts(ids=["b"], texts=["zyxqk words"])
    assert _text_rank(store, "zyxqk", "b") == 1
    store.close()


def test_in_place_update_is_detected_with_unchanged_count(db_path):
    store = _store(db_path)
    store.add_texts(ids=["a", "b"], texts=["alpha words", "beta words"])
    store.query("alpha", top_k=1)
    store.add_texts(ids=["a"], texts=["omega words"])
    assert store.count() == 2
    assert _text_rank(store, "omega", "a") == 1
    assert _text_rank(store, "alpha", "a") is None
    store.close()


def test_crashed_writer_leaves_a_file_the_next_search_repairs(db_path):
    _seed(db_path)
    writer = subprocess.run(
        [
            sys.executable,
            "-c",
            "import os, sys\n"
            "from duckdb_vectors.store import VectorStore\n"
            "s = VectorStore(db_path=sys.argv[1], backend='hashing')\n"
            "s.add_texts(ids=['late'], texts=['qwzrt crashtoken'])\n"
            "os._exit(0)\n",
            db_path,
        ],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert writer.returncode == 0, writer.stderr
    reader = _store(db_path)
    assert _text_rank(reader, "qwzrt", "late") == 1
    reader.close()


def test_deleted_chunks_stop_matching(db_path):
    store = _store(db_path)
    store.add_texts(ids=["a", "b"], texts=["alpha words", "gamma words"])
    store.query("gamma", top_k=1)
    store.delete_vectors(["b"])
    assert _text_rank(store, "gamma", "b") is None
    store.close()


def test_database_without_marker_counts_as_current(db_path, monkeypatch):
    store = _store(db_path)
    store.add_texts(ids=["a"], texts=["alpha words"])
    store.close()  # rebuilt and current
    legacy = duckdb.connect(db_path)
    legacy.execute("DROP TABLE index_state")
    legacy.close()
    rebuilds = _count_rebuilds(monkeypatch)
    reader = _store(db_path)
    assert _text_rank(reader, "alpha", "a") == 1
    reader.close()
    assert rebuilds[0] == 0


def test_rows_without_full_text_index_get_one(db_path):
    store = _store(db_path)
    store.add_texts(ids=["a"], texts=["alpha words"])
    store.close()
    bare = duckdb.connect(db_path)
    bare.execute("INSTALL fts")
    bare.execute("LOAD fts")
    bare.execute("PRAGMA drop_fts_index('chunks')")
    bare.execute("DROP TABLE index_state")
    bare.close()
    reader = _store(db_path)
    assert _text_rank(reader, "alpha", "a") == 1
    reader.close()


def test_vector_only_queries_never_rebuild(db_path, monkeypatch):
    _leave_stale(db_path, ["a"], ["alpha words"])
    rebuilds = _count_rebuilds(monkeypatch)
    store = _store(db_path)
    assert store.query("alpha", top_k=1, hybrid=False)
    assert rebuilds[0] == 0
    store.close()


def test_two_threads_rebuild_a_stale_index_once(db_path, monkeypatch):
    import threading

    _leave_stale(db_path, ["a", "b"], ["alpha words", "beta words"])
    rebuilds = _count_rebuilds(monkeypatch, delay=0.3)
    found = []

    def search():
        store = _store(db_path)
        try:
            found.append(_text_rank(store, "alpha", "a"))
        finally:
            store.close()

    threads = [threading.Thread(target=search) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)
    assert found == [1, 1]
    assert rebuilds[0] == 1


# Set-based writes: one DELETE and one INSERT per sub-batch, in a transaction.


def _rows(store):
    return store._con.execute(
        "SELECT chunk_id, text, source, position, title, metadata FROM chunks "
        "ORDER BY chunk_id"
    ).fetchall()


def test_batch_upsert_replaces_and_adds(db_path):
    store = _store(db_path)
    store.add_texts(ids=["a", "b"], texts=["alpha", "beta"])
    store.add_texts(ids=["b", "c"], texts=["beta two", "gamma"])
    assert [(r[0], r[1]) for r in _rows(store)] == [
        ("a", "alpha"),
        ("b", "beta two"),
        ("c", "gamma"),
    ]
    store.close()


def test_repeated_id_in_one_batch_keeps_last(db_path):
    store = _store(db_path)
    store.add_texts(ids=["a", "a", "b"], texts=["first", "last", "beta"])
    assert store.count() == 2
    assert store.get_vectors(["a"])[0].text == "last"
    store.close()


def test_batch_larger_than_sub_batch_is_written(db_path, monkeypatch):
    import duckdb_vectors.store as store_module

    monkeypatch.setattr(store_module, "_WRITE_BATCH", 3)
    store = _store(db_path)
    ids = [f"c{i}" for i in range(8)]
    store.add_texts(ids=ids, texts=[f"text {i}" for i in range(8)])
    assert store.count() == 8
    store.close()


def test_failed_batch_leaves_no_partial_rows(db_path):
    store = _store(db_path)
    store.add_texts(ids=["keep"], texts=["kept"])
    good = store.embedder.embed(["x"])[0]
    rows = [
        (Chunk(chunk_id="new1", text="one"), "e1", good),
        (Chunk(chunk_id="keep", text="replaced"), "e2", good),
        (Chunk(chunk_id="bad", text="bad"), "e3", good[:-1]),  # wrong width
    ]
    with pytest.raises(duckdb.Error):
        import datetime

        store._write_rows(rows, datetime.datetime.now(datetime.UTC))
    assert [(r[0], r[1]) for r in _rows(store)] == [("keep", "kept")]
    store.close()


def test_metadata_titles_and_positions_round_trip(db_path):
    store = _store(db_path)
    chunk = Chunk(
        chunk_id="doc.md:2",
        text="body text",
        source="doc.md",
        position=2,
        title="Heading",
        metadata={"kind": "policy", "tags": ["a", "b"]},
    )
    store.put_vectors([chunk])
    stored = store.get_vectors(["doc.md:2"])[0]
    assert (stored.source, stored.position, stored.title, stored.metadata) == (
        "doc.md",
        2,
        "Heading",
        {"kind": "policy", "tags": ["a", "b"]},
    )
    store.close()


def test_bulk_stamp_matches_previous_per_row_statement(db_path):
    import datetime

    store = _store(db_path)
    stamp = datetime.datetime.now(datetime.UTC)
    vector = store.embedder.embed(["x"])[0]
    store._write_rows([(Chunk(chunk_id="bulk", text="x"), "e", vector)], stamp)
    store._con.execute(
        "INSERT INTO chunks (chunk_id, text, stamped_at, embedding) "
        f"VALUES (?, ?, ?, ?::FLOAT[{store.dimensions}])",
        ["perrow", "x", stamp, vector],
    )
    stamps = dict(
        store._con.execute("SELECT chunk_id, stamped_at FROM chunks").fetchall()
    )
    assert stamps["bulk"] == stamps["perrow"]
    store.close()
