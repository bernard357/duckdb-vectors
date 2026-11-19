"""Build-time indexer for the Lambda RAG image.

Embeds the documentation with Amazon Titan Embed and writes the DuckDB index
into the image. Run during `docker build` with AWS credentials available.
"""

import sys

from duckdb_vectors import VectorStore, index_folder


def main() -> None:
    docs, db_path = sys.argv[1], sys.argv[2]
    store = VectorStore(db_path=db_path, backend="bedrock:titan")
    try:
        report = index_folder(store, docs, recursive=True)
        print(f"indexed {report.files} files, {report.chunks} chunks")
    finally:
        store.close()


if __name__ == "__main__":
    main()
