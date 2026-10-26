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

from mcp.server.mcpserver import MCPServer

from duckdb_vectors.config import Settings
from duckdb_vectors.store import VectorStore


def build_server(settings: Settings | None = None) -> MCPServer:
    """Build the MCP server and register the read-only retrieval tools.

    A single :class:`Settings` is resolved once (honoring the
    ``DUCKDB_VECTORS_*`` environment variables and any ``db``/``backend`` passed
    from the CLI verb) and reused by every tool call, each opening its own
    :class:`VectorStore`.
    """
    resolved = settings or Settings()
    server: MCPServer = MCPServer(name="duckdb-vectors")

    @server.tool()
    def search(query: str, top_k: int = 5) -> list[dict]:
        """Search the knowledge base for passages relevant to a query.

        Runs hybrid retrieval over a local document index — semantic (vector)
        search for meaning plus exact-token (keyword) search for literal
        strings. Reach for it to ground a request that goes beyond the agent's
        general knowledge, or one that contains acronyms, product codes,
        identifiers, or other keywords that must match literally. Returns a
        JSON array of the top-ranked chunks, best first, each an object with
        ``chunk_id``, ``text``, ``source``, ``title``, ``metadata`` (the
        chunk's frontmatter/attributes), and ``score``.
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

        Returns meta-information about the index rather than its content: a
        JSON object with the database path, the embedding backend, the vector
        dimensions, the chunk count, and the list of sources. Use it to
        evaluate whether the knowledge base is worth searching and what topics
        it covers before or instead of a ``search`` call.
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
