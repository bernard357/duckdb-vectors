"""MCP-over-stdio server exposing duckdb-vectors retrieval as tools.

Any MCP client (Docker Agent, Claude Desktop, Claude Code, Cursor) can spawn
``duckdb-vectors mcp`` as a subprocess and call the ``search`` tool. The server
is built on the official ``mcp`` SDK.

Note on the import path: in the ``mcp`` 2.x SDK the server class formerly named
``FastMCP`` is ``MCPServer`` (``from mcp.server.mcpserver import MCPServer``);
its ``.run(transport="stdio")`` serves over stdio, matching the subprocess
model MCP clients use.

Both tools open a :class:`VectorStore` on the resolved :class:`Settings` for
each call; the store opens writable (its single mode) and is used read-only by
these tools.
"""

from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _dist_version

from mcp.server.mcpserver import MCPServer

from duckdb_vectors.config import Settings
from duckdb_vectors.store import VectorStore

_SERVER_DESCRIPTION = (
    "A local, embedded knowledge base over a DuckDB index, searched with hybrid "
    "semantic (vector) and exact-token (keyword) retrieval."
)

_SERVER_INSTRUCTIONS = (
    "This server fronts a local, read-only document knowledge base. Reach for it "
    "when a request needs grounding beyond your general knowledge, or contains "
    "acronyms, product codes, identifiers, or other keywords to match literally. "
    "Call `report` first to see whether the index covers the topic and what its "
    "sources are, then `search` to retrieve the ranked chunks. Both tools only "
    "read the index — they never write or mutate it — and return JSON."
)


def _resolve_version() -> str:
    """Return the installed package version, or ``""`` in a source checkout.

    The version is read from the distribution metadata so an MCP client sees the
    real ``vYYMMDD``-derived version at ``initialize``. An editable or source
    checkout without installed metadata has no version; fall back to an empty
    string so the server always builds.
    """
    try:
        return _dist_version("duckdb-vectors")
    except PackageNotFoundError:
        return ""


def build_server(settings: Settings | None = None) -> MCPServer:
    """Build the MCP server and register the read-only retrieval tools.

    A single :class:`Settings` is resolved once (honoring the
    ``DUCKDB_VECTORS_*`` environment variables and any ``db``/``backend`` passed
    from the CLI verb) and reused by every tool call, each opening its own
    :class:`VectorStore`.

    The server carries a ``description`` and an ``instructions`` block so an MCP
    client gets whole-server framing (scope with ``report``, ground with
    ``search``; read-only; JSON results) at the ``initialize`` handshake, not
    only the per-tool docstrings, plus the installed package ``version``.
    """
    resolved = settings or Settings()
    server: MCPServer = MCPServer(
        name="duckdb-vectors",
        description=_SERVER_DESCRIPTION,
        instructions=_SERVER_INSTRUCTIONS,
        version=_resolve_version(),
    )

    @server.tool()
    def search(query: str, top_k: int = 5) -> list[dict]:
        """Search the knowledge base for passages relevant to a query.

        Runs hybrid retrieval over a local document index — semantic (vector)
        search for meaning plus exact-token (keyword) search for literal
        strings — and fuses the two rankings with Reciprocal Rank Fusion.
        Reach for it to ground a request that goes beyond the agent's general
        knowledge, or one that contains acronyms, product codes, identifiers,
        or other keywords that must match literally.

        Args:
            query: The natural-language question or keyword phrase to match.
                Both its meaning (via the embedding) and its literal tokens
                (via keyword search) are used, so plain questions and exact
                identifiers both work.
            top_k: The maximum number of chunks to return, best first
                (default 5).

        Returns a JSON array of the top-ranked chunks, best first. Each element
        is an object with these fields:

        - ``chunk_id`` (str): stable identifier of the chunk, derived from its
          source and position at index time. Re-indexing the same position
          reuses the same id, so it is safe to use for de-duplication or to
          refer back to a passage.
        - ``text`` (str): the chunk's text — the passage itself, to read or
          quote as grounding. This is the only field carrying the retrieved
          content.
        - ``source`` (str): where the chunk came from, typically the indexed
          file path or document identifier. Empty string when the source was
          not recorded at index time. Use it to cite or group by document.
        - ``title`` (str): a human-readable title for the source (e.g. a
          document or section heading), or empty string when none was captured.
        - ``metadata`` (object): the chunk's frontmatter and custom attributes
          as captured during indexing (for example ``audience``, ``kind``,
          ``date``, ``tags``). An empty object when the chunk carried none.
          Use it to filter or prioritise hits by attribute.
        - ``score`` (number): the fused relevance score, higher is better. It
          is a Reciprocal Rank Fusion score (a small positive float, the sum of
          ``1 / (60 + rank)`` over the signals that surfaced the chunk), not a
          0-to-1 probability — compare scores only relative to each other
          within one result set, not against an absolute threshold.
        """
        store = VectorStore(settings=resolved)
        try:
            results = store.query(query, top_k=top_k)
        finally:
            store.close()
        return [
            {
                "chunk_id": result.chunk.chunk_id,
                "text": result.chunk.text,
                "source": result.chunk.source,
                "title": result.chunk.title,
                "metadata": result.chunk.metadata,
                "score": result.score,
            }
            for result in results
        ]

    @server.tool()
    def report() -> dict:
        """Describe the knowledge base itself, to judge what it contains.

        Returns meta-information about the index rather than its content. Use
        it to evaluate whether the knowledge base is worth searching and what
        topics it covers before or instead of a ``search`` call.

        Returns a JSON object with these fields:

        - ``db_path`` (str): filesystem path of the DuckDB database file backing
          the index. Identifies which local store is being served.
        - ``backend`` (str): the embedding backend that produced the vectors
          (for example a local model name or ``hashing``). Queries must embed
          with the same backend, so this tells the agent how the content was
          encoded.
        - ``dimensions`` (int): the embedding vector width, in floats. A
          property of the backend; mainly diagnostic.
        - ``chunks`` (int): the total number of chunks (indexed passages)
          currently stored. Zero means an empty index — nothing to search.
        - ``sources`` (array of str): the sorted, de-duplicated list of distinct
          ``source`` values across all chunks (the documents or files indexed).
          Scan it to judge whether the corpus covers the topic at hand before
          spending a ``search`` call.
        """
        store = VectorStore(settings=resolved)
        try:
            chunks = store.list_vectors()
            sources = sorted({chunk.source for chunk in chunks if chunk.source})
            return {
                "db_path": store.settings.db_path,
                "backend": store.settings.backend,
                "dimensions": store.dimensions,
                "chunks": len(chunks),
                "sources": sources,
            }
        finally:
            store.close()

    return server


def run_stdio(
    settings: Settings | None = None,
    index_folder: str | None = None,
    recursive: bool = False,
) -> None:
    """Build the server and serve it over stdio until the client disconnects.

    When ``index_folder`` is given, the target folder is indexed into the
    database before the server starts, so a client can launch a self-contained
    server with no separate indexing step. Indexing is incremental (etag reuse),
    so an unchanged folder is near-instant on restart. The index report is
    written to stderr; stdout is reserved for the MCP protocol.
    """
    resolved = settings or Settings()
    if index_folder is not None:
        _index_before_serving(resolved, index_folder, recursive)
    build_server(resolved).run(transport="stdio")


def _index_before_serving(settings: Settings, folder: str, recursive: bool) -> None:
    """Index ``folder`` into the configured database, logging to stderr."""
    import sys

    from duckdb_vectors.indexer import index_folder as run_index

    store = VectorStore(settings=settings)
    try:
        report = run_index(store, folder, recursive=recursive)
    finally:
        store.close()
    print(
        f"Indexed {report.files} files, {report.chunks} chunks "
        f"({report.embedded} embedded, {report.reused} reused, "
        f"{report.pruned} pruned) from {folder}.",
        file=sys.stderr,
        flush=True,
    )
