"""MCP-over-stdio server exposing duckdb-vectors retrieval as tools.

Any MCP client (Docker Agent, Claude Desktop, Claude Code, Cursor) can spawn
``duckdb-vectors mcp`` as a subprocess and call the ``search`` tool. The server
is built on the official ``mcp`` SDK.

Note on the import path: in the ``mcp`` 2.x SDK the server class formerly named
``FastMCP`` is ``MCPServer`` (``from mcp.server.mcpserver import MCPServer``);
its ``.run(transport="stdio")`` serves over stdio, matching the subprocess
model MCP clients use.

Both tools open a :class:`VectorStore` on the resolved :class:`Settings` for
each call, sharing one embedder for the server's lifetime so the model loads
once. The store opens writable (its single mode) and ensures its schema,
so a call can create the database file and its tables when they are missing;
the tools never add, change, or delete indexed chunks.
"""

import threading
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _dist_version

from mcp.server.mcpserver import MCPServer

from duckdb_vectors.config import Settings
from duckdb_vectors.embedders.base import Embedder
from duckdb_vectors.embedders.registry import resolve_embedder
from duckdb_vectors.store import VectorStore

_SERVER_DESCRIPTION = (
    "A local, embedded knowledge base over a DuckDB index, searched with hybrid "
    "semantic (vector) and exact-token (keyword) retrieval."
)

_SERVER_INSTRUCTIONS = (
    "This server fronts a local document knowledge base. Reach for it when a "
    "request needs grounding beyond your general knowledge, or contains "
    "acronyms, product codes, identifiers, or other keywords to match literally. "
    "Call `report` first to see whether the index covers the topic and what its "
    "sources are, then `search` to retrieve the ranked chunks. Both tools return "
    "JSON and never change the indexed documents; opening the index can create "
    "an empty database file if none exists. `search` embeds your query with the "
    "configured backend, which runs locally by default."
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


class _SharedEmbedder:
    """One embedder for the server's lifetime, loaded once under a lock.

    Building a fresh embedder per tool call reloaded the model on every search
    (about 1.15 s with the default backend). The server keeps a single instance
    instead, while each call still opens and closes its own store, so the
    database file lock stays as short as before. Tools run in worker threads,
    so the first load is serialized: concurrent first calls load the model
    once. Later calls skip the lock; ONNX Runtime and boto3 clients are
    thread-safe, and the hashing embedder is stateless.
    """

    def __init__(self, embedder: Embedder) -> None:
        self._embedder = embedder
        self._lock = threading.Lock()
        self._dimensions_known = False
        self._loaded = False

    def for_store(self) -> Embedder:
        """Return the embedder with its dimension resolved.

        Backends whose dimension is not known up front probe it with a real
        embed, which loads the model; that probe runs once, under the lock.
        """
        if not self._dimensions_known:
            with self._lock:
                if not self._dimensions_known:
                    _ = self._embedder.dimensions
                    self._dimensions_known = True
        return self._embedder

    def for_query(self) -> Embedder:
        """Return the embedder with its model loaded, loading it once."""
        if not self._loaded:
            with self._lock:
                if not self._loaded:
                    self._embedder.embed(["warm-up"])
                    self._dimensions_known = True
                    self._loaded = True
        return self._embedder


def build_server(
    settings: Settings | None = None,
    embedder: Embedder | None = None,
) -> MCPServer:
    """Build the MCP server and register the two retrieval tools.

    A single :class:`Settings` is resolved once (honoring the
    ``DUCKDB_VECTORS_*`` environment variables and any ``db``/``backend`` passed
    from the CLI verb) and reused by every tool call. One embedder is resolved
    once too (or taken from ``embedder``) and shared by every call, so the
    model loads on the first search and stays loaded; each call still opens its
    own :class:`VectorStore` and closes it when done.

    The server carries a ``description`` and an ``instructions`` block so an MCP
    client gets whole-server framing (scope with ``report``, ground with
    ``search``; documents never changed; JSON results) at the ``initialize``
    handshake, not
    only the per-tool docstrings, plus the installed package ``version``.
    """
    resolved = settings or Settings()
    shared = _SharedEmbedder(embedder or resolve_embedder(resolved.backend))
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
        - ``source`` (str): where the chunk came from — the folder-relative
          file path the indexer assigns to every chunk it ingests. Populated by
          indexing, so a served knowledge base always carries it; use it to
          cite or group hits by document. (Empty only for a chunk written
          directly through the SDK without a source.)
        - ``title`` (str): a human-readable title the indexer derives from the
          document's structure — the Markdown section heading, or ``Page N``
          for a PDF page. Populated by indexing; empty only for a chunk written
          directly through the SDK without a title.
        - ``metadata`` (object): attributes the indexer attaches to the chunk —
          the source document's frontmatter (for example ``audience``,
          ``kind``, ``date``, ``tags``) plus the ``section_path`` of the
          heading the chunk sits under for Markdown. An empty object when the
          document carried no such attributes. Use it to filter or prioritise
          hits by attribute.
        - ``score`` (number): the fused relevance score, higher is better. It
          is a Reciprocal Rank Fusion score (a small positive float, the sum of
          ``1 / (60 + rank)`` over the signals that surfaced the chunk), not a
          0-to-1 probability — compare scores only relative to each other
          within one result set, not against an absolute threshold.
        """
        store = VectorStore(settings=resolved, embedder=shared.for_query())
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
        store = VectorStore(settings=resolved, embedder=shared.for_store())
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
    server with no separate indexing step. Indexing is incremental (etag reuse):
    an unchanged folder is re-chunked but nothing is re-embedded or rewritten,
    and the database file is locked only for the brief lookup and writes, so
    other sessions sharing it keep querying. The index report is written to
    stderr; stdout is reserved for the MCP protocol.
    """
    resolved = settings or Settings()
    embedder = resolve_embedder(resolved.backend)
    if index_folder is not None:
        _index_before_serving(resolved, index_folder, recursive, embedder)
    build_server(resolved, embedder=embedder).run(transport="stdio")


def _index_before_serving(
    settings: Settings,
    folder: str,
    recursive: bool,
    embedder: Embedder | None = None,
) -> None:
    """Index ``folder`` into the configured database, logging to stderr.

    Pass the server's ``embedder`` so a model loaded while indexing stays
    loaded for the searches that follow.
    """
    import sys

    from duckdb_vectors.indexer import index_folder as run_index

    store = VectorStore(settings=settings, embedder=embedder)
    try:
        report = run_index(store, folder, recursive=recursive)
    finally:
        store.close()
    print(
        f"Indexed {report.files} files, {report.chunks} chunks "
        f"({report.embedded} embedded, {report.reused} reused, "
        f"{report.written} written, {report.pruned} pruned) from {folder}.",
        file=sys.stderr,
        flush=True,
    )
