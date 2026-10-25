"""Index a folder of PDFs with the pdf chunker.

Indexing uses the composite chunker by default, which dispatches each file to
the registered chunker claiming its MIME type. This example forces PDF-only
indexing by passing an explicit ``PdfChunker`` through the code-level
``chunker=`` override. The PDF chunker extracts per-page text, falls back to
local OCR for scanned pages, and emits one chunk per page window.

Usage::

    python build_pdf_index.py ./pdfs vectors.duckdb
"""

import sys

from duckdb_vectors import PdfChunker, VectorStore, index_folder


def main() -> None:
    pdf_folder, db_path = sys.argv[1], sys.argv[2]
    store = VectorStore(
        db_path=db_path,
        backend="fastembed:minishlab/potion-multilingual-128M",
    )
    try:
        report = index_folder(store, pdf_folder, chunker=PdfChunker(), recursive=True)
        print(f"indexed {report.files} files, {report.chunks} chunks")
    finally:
        store.close()


if __name__ == "__main__":
    main()
