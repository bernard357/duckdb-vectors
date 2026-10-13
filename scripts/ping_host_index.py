"""Index the documentation with Amazon Titan on the host for the AWS pings.

Called from the shell ping snippets so AWS credentials never enter a Docker
image layer. Writes a Titan-embedded DuckDB index to the given path.

Usage::

    python scripts/ping_host_index.py <docs-dir> <db-path>
"""

import sys

from duckdb_vectors import VectorStore, index_folder


def main() -> int:
    docs, db_path = sys.argv[1], sys.argv[2]
    store = VectorStore(db_path=db_path, backend="bedrock:titan")
    try:
        rep = index_folder(store, docs, recursive=True)
        print(f"host-indexed {rep.files} files, {rep.chunks} chunks")
    finally:
        store.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
