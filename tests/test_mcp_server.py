"""In-process tests for the MCP stdio server's search and report tools.

Offline and deterministic: the ``hashing`` backend needs no model download and
no network. Tools are exercised through the server's own ``call_tool`` /
``list_tools`` API (no real subprocess, no stdio pipe).
"""

import asyncio
import json

import pytest

from duckdb_vectors.config import Settings
from duckdb_vectors.mcp_server import build_server
from duckdb_vectors.store import VectorStore


@pytest.fixture
def indexed_db(tmp_path):
    db = str(tmp_path / "mcp.duckdb")
    writer = VectorStore(db_path=db, backend="hashing")
    writer.add_texts(
        ids=["fox", "dog", "db"],
        texts=[
            "the quick brown fox jumps",
            "a lazy dog sleeps all day",
            "duckdb analytical database engine",
        ],
        metadatas=[{}, {}, {"kind": "reference", "topic": "database"}],
        titles=["Fox", "Dog", "DuckDB"],
    )
    writer.close()
    return db


def _server(db):
    return build_server(Settings(db_path=db, backend="hashing"))


def test_tools_are_registered(indexed_db):
    server = _server(indexed_db)
    names = {tool.name for tool in asyncio.run(server.list_tools())}
    assert {"search", "report"} <= names


def test_tool_descriptions_are_non_trivial(indexed_db):
    server = _server(indexed_db)
    tools = {
        tool.name: tool.description or "" for tool in asyncio.run(server.list_tools())
    }
    assert "knowledge base" in tools["search"].lower()
    assert "keyword" in tools["search"].lower()
    assert "knowledge base" in tools["report"].lower()


def test_server_carries_description_and_instructions(indexed_db):
    """The server is self-describing at the MCP `initialize` handshake.

    Beyond the per-tool docstrings, the client sees a server-level
    `description` and an `instructions` block framing the two tools as a pair
    (scope with `report`, ground with `search`; read-only; JSON).
    """
    server = _server(indexed_db)
    assert "knowledge base" in (server.description or "").lower()
    instructions = (server.instructions or "").lower()
    assert "report" in instructions
    assert "search" in instructions
    assert "read-only" in instructions


def test_server_version_resolution_is_graceful(monkeypatch):
    """`build_server` never raises when distribution metadata is absent."""
    from importlib.metadata import PackageNotFoundError

    import duckdb_vectors.mcp_server as mcp_server

    def _missing(_name):
        raise PackageNotFoundError

    monkeypatch.setattr(mcp_server, "_dist_version", _missing)
    assert mcp_server._resolve_version() == ""
    server = mcp_server.build_server(Settings(backend="hashing"))
    assert server.version == ""


def test_search_tool_returns_ranked_hits(indexed_db):
    server = _server(indexed_db)
    result = asyncio.run(
        server.call_tool("search", {"query": "analytical database", "top_k": 3})
    )
    assert result.is_error is False
    hits = result.structured_content["result"]
    assert hits[0]["chunk_id"] == "db"
    assert {"chunk_id", "text", "source", "title", "metadata", "score"} == set(hits[0])
    # metadata round-trips, and internal scoring is not exposed.
    assert hits[0]["metadata"] == {"kind": "reference", "topic": "database"}
    assert "vector_rank" not in hits[0]
    assert "text_rank" not in hits[0]


def test_search_tool_respects_top_k(indexed_db):
    server = _server(indexed_db)
    result = asyncio.run(server.call_tool("search", {"query": "database", "top_k": 1}))
    assert len(result.structured_content["result"]) == 1


def test_report_tool_describes_index(indexed_db):
    server = _server(indexed_db)
    result = asyncio.run(server.call_tool("report", {}))
    assert result.is_error is False
    payload = json.loads(result.content[0].text)
    assert payload["backend"] == "hashing"
    assert payload["chunks"] == 3
    assert payload["dimensions"] == 384


def test_index_before_serving_builds_the_database(tmp_path, capsys):
    """`mcp --index <folder>` indexes the folder into the DB before serving.

    Exercises the startup-index path (`_index_before_serving`) directly: a
    fresh DB is built from a markdown folder, a reopened store sees the chunks,
    and the index report is written to stderr (stdout stays clean for MCP).
    """
    from duckdb_vectors.mcp_server import _index_before_serving

    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "a.md").write_text("# Alpha\n\nThe quick brown fox jumps.\n")
    (docs / "b.md").write_text("# Beta\n\nDuckDB is an analytical database.\n")
    db = str(tmp_path / "served.duckdb")

    settings = Settings(db_path=db, backend="hashing")
    _index_before_serving(settings, str(docs), recursive=False)

    reopened = VectorStore(settings=Settings(db_path=db, backend="hashing"))
    try:
        assert reopened.count() >= 2
    finally:
        reopened.close()

    err = capsys.readouterr().err
    assert "Indexed" in err  # report went to stderr, not stdout
    assert capsys.readouterr().out == ""


def test_index_before_serving_is_incremental(tmp_path):
    """A second index of an unchanged folder re-embeds nothing (etag reuse)."""
    from duckdb_vectors.indexer import index_folder as run_index

    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "a.md").write_text("# Alpha\n\nThe quick brown fox jumps.\n")
    db = str(tmp_path / "served.duckdb")

    store = VectorStore(db_path=db, backend="hashing")
    try:
        first = run_index(store, str(docs))
        second = run_index(store, str(docs))
    finally:
        store.close()
    assert first.embedded > 0
    assert second.embedded == 0
    assert second.reused >= first.chunks
