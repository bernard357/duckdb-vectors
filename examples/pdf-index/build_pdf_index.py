"""Index a folder of PDFs with the pdf chunker.

The pdf chunker is selected the same way markdown is — by the ``chunker=``
setting (or the ``DUCKDB_VECTORS_CHUNKER`` environment variable). It extracts
per-page text, falls back to local OCR for scanned pages, and emits one chunk
per page window.

Usage::

    python build_pdf_index.py ./pdfs vectors.duckdb
"""

import sys

from duckdb_vectors import VectorStore, index_folder


def main() -> None:
    pdf_folder, db_path = sys.argv[1], sys.argv[2]
    store = VectorStore(
        db_path=db_path,
        backend="fastembed:minishlab/potion-multilingual-128M",
        chunker="pdf",
    )
    try:
        report = index_folder(store, pdf_folder, recursive=True)
        print(f"indexed {report.files} files, {report.chunks} chunks")
    finally:
        store.close()


if __name__ == "__main__":
    main()
