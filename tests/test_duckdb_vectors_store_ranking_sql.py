"""Injectable ranking SQL: scoping, decay, precedence, and q binding."""

from unittest import mock

from duckdb_vectors.config import Settings
from duckdb_vectors.models import Chunk
from duckdb_vectors.store import VectorStore


def _store(tmp_path, **kwargs):
    return VectorStore(
        db_path=str(tmp_path / "rank.duckdb"), backend="hashing", **kwargs
    )


def _seed(store):
    store.put_vectors(
        [
            Chunk(
                chunk_id="keep-new",
                text="database replication tuning for policies",
                metadata={"kind": "policy", "age_days": "1"},
            ),
            Chunk(
                chunk_id="keep-old",
                text="database replication tuning for policies archived",
                metadata={"kind": "policy", "age_days": "3650"},
            ),
            Chunk(
                chunk_id="skip",
                text="database replication tuning note",
                metadata={"kind": "scratch", "age_days": "1"},
            ),
        ]
    )


def test_default_behavior_unchanged(tmp_path):
    store = _store(tmp_path)
    _seed(store)
    results = store.query("database replication tuning", top_k=5)
    assert {r.chunk.chunk_id for r in results} == {
        "keep-new",
        "keep-old",
        "skip",
    }
    store.close()


def test_custom_vector_sql_scopes_out_of_scope_chunks(tmp_path):
    store = _store(tmp_path)
    _seed(store)
    scoped = (
        "SELECT chunk_id FROM {table} "
        "WHERE json_extract_string(metadata, '$.kind') = 'policy' "
        "ORDER BY {distance} LIMIT {top_k}"
    )
    results = store.query(
        "database replication tuning",
        top_k=5,
        vector_sql=scoped,
        text_sql=scoped.replace("{distance}", "random()"),
    )
    ids = {r.chunk.chunk_id for r in results}
    assert "skip" not in ids
    assert ids <= {"keep-new", "keep-old"}
    store.close()


def test_custom_vector_sql_decays_aging_documents(tmp_path):
    store = _store(tmp_path)
    _seed(store)
    # Multiply distance by a factor that grows with age so older docs sink.
    decayed = (
        "SELECT chunk_id FROM {table} "
        "WHERE json_extract_string(metadata, '$.kind') = 'policy' "
        "ORDER BY {distance} "
        "* (1 + CAST(json_extract_string(metadata, '$.age_days') AS DOUBLE) "
        "/ 365.0) "
        "LIMIT {top_k}"
    )
    results = store.query(
        "database replication tuning",
        top_k=5,
        hybrid=False,
        vector_sql=decayed,
    )
    ids = [r.chunk.chunk_id for r in results]
    assert ids.index("keep-new") < ids.index("keep-old")
    store.close()


def test_q_binding_reaches_text_template(tmp_path):
    store = _store(tmp_path)
    _seed(store)
    # The template references $q via the {bm25} token; a matching term should
    # surface a chunk, a non-matching term should not.
    text_only = (
        "SELECT chunk_id FROM ("
        "SELECT chunk_id, {bm25} AS score FROM {table}"
        ") WHERE score IS NOT NULL ORDER BY score DESC LIMIT {top_k}"
    )
    hit = store.query("archived", top_k=5, hybrid=True, text_sql=text_only)
    assert any(r.text_rank for r in hit)
    store.close()


def test_settings_override_is_shared(tmp_path):
    scoped = (
        "SELECT chunk_id FROM {table} "
        "WHERE json_extract_string(metadata, '$.kind') = 'policy' "
        "ORDER BY {distance} LIMIT {top_k}"
    )
    settings = Settings(
        db_path=str(tmp_path / "rank.duckdb"),
        backend="hashing",
        vector_sql=scoped,
        text_sql=scoped,
    )
    store = VectorStore(settings=settings)
    _seed(store)
    results = store.query("database replication tuning", top_k=5)
    assert "skip" not in {r.chunk.chunk_id for r in results}
    store.close()


def test_settings_reads_env():
    env = {
        "DUCKDB_VECTORS_VECTOR_SQL": "SELECT chunk_id FROM {table}",
        "DUCKDB_VECTORS_TEXT_SQL": "SELECT chunk_id FROM {table}",
    }
    with mock.patch.dict("os.environ", env, clear=True):
        settings = Settings()
    assert settings.vector_sql == "SELECT chunk_id FROM {table}"
    assert settings.text_sql == "SELECT chunk_id FROM {table}"


def test_per_call_overrides_settings(tmp_path):
    # Settings scopes to policy; a per-call template scopes to scratch instead.
    settings = Settings(
        db_path=str(tmp_path / "rank.duckdb"),
        backend="hashing",
        vector_sql=(
            "SELECT chunk_id FROM {table} "
            "WHERE json_extract_string(metadata, '$.kind') = 'policy' "
            "ORDER BY {distance} LIMIT {top_k}"
        ),
    )
    store = VectorStore(settings=settings)
    _seed(store)
    per_call = (
        "SELECT chunk_id FROM {table} "
        "WHERE json_extract_string(metadata, '$.kind') = 'scratch' "
        "ORDER BY {distance} LIMIT {top_k}"
    )
    results = store.query_vectors(
        store.embedder.embed(["database"])[0],
        top_k=5,
        vector_sql=per_call,
    )
    assert {r.chunk.chunk_id for r in results} == {"skip"}
    store.close()
