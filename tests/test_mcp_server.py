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
        metadatas=[{}, {}, {}],
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


def test_search_tool_returns_ranked_hits(indexed_db):
    server = _server(indexed_db)
    result = asyncio.run(
        server.call_tool("search", {"query": "analytical database", "top_k": 3})
    )
    assert result.is_error is False
    hits = result.structured_content["result"]
    assert hits[0]["chunk_id"] == "db"
    assert {"chunk_id", "text", "source", "title", "score"} <= set(hits[0])


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
