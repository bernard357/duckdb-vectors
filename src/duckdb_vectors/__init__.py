"""duckdb-vectors: an embeddable semantic-search building block.

Exposes a simplified, S3-Vectors-flavored interface over a local DuckDB
database with hybrid vector + BM25 retrieval. One DuckDB file is one index.
"""

from duckdb_vectors.chunkers.base import Chunker
from duckdb_vectors.chunkers.composite import CompositeChunker
from duckdb_vectors.chunkers.markdown import MarkdownChunker
from duckdb_vectors.chunkers.pdf import PdfChunker
from duckdb_vectors.chunkers.registry import register_chunker, resolve_chunker
from duckdb_vectors.config import Settings
from duckdb_vectors.embedders.base import Embedder
from duckdb_vectors.embedders.registry import register_embedder, resolve_embedder
from duckdb_vectors.etag import compute_etag
from duckdb_vectors.indexer import IndexReport, index_folder
from duckdb_vectors.mcp_server import build_server
from duckdb_vectors.models import Chunk, SearchResult
from duckdb_vectors.store import VectorStore

__all__ = [
    "Chunk",
    "Chunker",
    "CompositeChunker",
    "Embedder",
    "IndexReport",
    "MarkdownChunker",
    "PdfChunker",
    "SearchResult",
    "Settings",
    "VectorStore",
    "build_server",
    "compute_etag",
    "index_folder",
    "register_chunker",
    "register_embedder",
    "resolve_chunker",
    "resolve_embedder",
]
