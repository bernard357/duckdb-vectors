"""MCP-over-stdio server exposing duckdb-vectors retrieval as tools.

Any MCP client (Docker Agent, Claude Desktop, Claude Code, Cursor) can spawn
``duckdb-vectors mcp`` as a subprocess and call the ``search`` tool. The server
is built on the official ``mcp`` SDK.

Note on the import path: in the ``mcp`` 2.x SDK the server class formerly named
``FastMCP`` is ``MCPServer`` (``from mcp.server.mcpserver import MCPServer``);
its ``.run(transport="stdio")`` serves over stdio, matching the subprocess
model MCP clients use.

The serving contract is read-only: both tools open the resolved
:class:`Settings` with ``read_only=True``, mirroring the baked-index lifecycle.
"""

from mcp.server.mcpserver import MCPServer

from duckdb_vectors.config import Settings
from duckdb_vectors.store import VectorStore


def build_server(settings: Settings | None = None) -> MCPServer:
    """Build the MCP server and register the read-only retrieval tools.

    A single :class:`Settings` is resolved once (honoring the
    ``DUCKDB_VECTORS_*`` environment variables and any ``db``/``backend`` passed
    from the CLI verb) and reused by every tool call, each opening its own
    read-only :class:`VectorStore`.
    """
    resolved = settings or Settings()
    server: MCPServer = MCPServer(name="duckdb-vectors")

    @server.tool()
    def search(query: str, top_k: int = 5) -> list[dict]:
        """Hybrid semantic + exact-token search; returns ranked chunks."""
        store = VectorStore(settings=_read_only(resolved))
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
                "score": result.score,
            }
            for result in results
        ]

    @server.tool()
    def report() -> dict:
        """Introspect the index: db path, backend, dimensions, chunks, sources."""
        store = VectorStore(settings=_read_only(resolved))
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


def _read_only(settings: Settings) -> Settings:
    """Return a copy of ``settings`` forced to open the store read-only."""
    return Settings(
        db_path=settings.db_path,
        backend=settings.backend,
        read_only=True,
        vector_sql=settings.vector_sql,
        text_sql=settings.text_sql,
    )


def run_stdio(settings: Settings | None = None) -> None:
    """Build the server and serve it over stdio until the client disconnects."""
    build_server(settings).run(transport="stdio")
