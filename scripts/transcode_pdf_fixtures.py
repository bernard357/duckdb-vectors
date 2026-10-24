"""Transcode each fixture PDF to a companion markdown baseline.

Run once (and again whenever the PDF fixtures change) to freeze the expected
extraction for the non-regression test in ``tests/test_pdf_fixtures.py``. For
every ``fixtures/pdf/*.pdf`` this runs the real :class:`PdfChunker` and writes a
sibling ``*.md`` holding the chunker's output per page: one ``## Page N``
section per page, followed by that page's chunk windows in order. The test
re-runs the chunker and asserts the reconstruction still equals the committed
markdown, so any drift in text extraction, windowing, or page handling is
caught.

The fixtures shipped here are born-digital (every page has an extractable text
layer), so this exercises the text path deterministically; the OCR fallback is
covered separately by the stubbed unit tests in ``tests/test_pdf_chunker.py``.

Usage (from the repo root, with the PDF deps installed):

    $ .venv/bin/python scripts/transcode_pdf_fixtures.py
"""

from pathlib import Path

from duckdb_vectors.chunkers.pdf import PdfChunker

FIXTURE_DIR = Path(__file__).resolve().parent.parent / "fixtures" / "pdf"


def transcode(pdf: Path) -> str:
    """Return the markdown baseline for one PDF via the real chunker."""
    chunker = PdfChunker()
    chunker.begin_indexing()
    try:
        chunks = list(chunker.chunk_file(pdf))
    finally:
        chunker.end_indexing()

    lines: list[str] = [f"# {pdf.name}", ""]
    current_page: int | None = None
    for chunk in chunks:
        page = chunk.metadata["page"]
        if page != current_page:
            lines.append(f"## Page {page}")
            lines.append("")
            current_page = page
        lines.append(chunk.text)
        lines.append("")
    return "\n".join(lines).rstrip("\n") + "\n"


def main() -> int:
    pdfs = sorted(FIXTURE_DIR.glob("*.pdf"))
    if not pdfs:
        print(f"no PDFs found under {FIXTURE_DIR}")
        return 1
    for pdf in pdfs:
        companion = pdf.with_suffix(".md")
        companion.write_text(transcode(pdf), encoding="utf-8")
        print(f"wrote {companion.relative_to(FIXTURE_DIR.parent.parent)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
