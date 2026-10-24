"""Non-regression tests for PDF ingestion over real documents.

The fixtures under ``fixtures/pdf/`` are born-digital PDFs with a committed
companion ``*.md`` baseline produced by ``scripts/transcode_pdf_fixtures.py``.
Each test re-runs the real :class:`PdfChunker` and asserts its output still
reconstructs the committed markdown, so any drift in text extraction,
windowing, or page handling is caught. OCR is not exercised here (these pages
all carry a text layer); the OCR fallback is covered by the stubbed unit tests
in ``tests/test_pdf_chunker.py``.

The suite skips cleanly when the PDF dependencies are not installed.
"""

import sys
from pathlib import Path

import pytest

pytest.importorskip("pypdf")

REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURE_DIR = REPO_ROOT / "fixtures" / "pdf"

# Reuse the transcoder so the baseline and the assertion share one code path.
sys.path.insert(0, str(REPO_ROOT / "scripts"))
from transcode_pdf_fixtures import transcode  # noqa: E402

pytestmark = pytest.mark.slow

# The synthetic scanned fixture exercises the OCR path; OCR output is not
# byte-stable across engine versions, so it is excluded from the exact-match
# baseline test and covered by a token-recovery test instead.
_OCR_FIXTURE = FIXTURE_DIR / "synthetic-scanned.pdf"
_PDFS = sorted(FIXTURE_DIR.glob("*.pdf"))
_BORN_DIGITAL = [p for p in _PDFS if p != _OCR_FIXTURE]

# Marker tokens rendered into the synthetic scanned fixture (see
# scripts/make_ocr_fixture.py); robust enough to assert OCR recovered them.
_OCR_TOKENS = ("esrs", "alpha", "bravo", "charlie", "delta", "echo")


def test_fixture_pdfs_present():
    """The committed born-digital fixtures and their baselines must exist."""
    assert _BORN_DIGITAL, f"no born-digital PDF fixtures under {FIXTURE_DIR}"
    for pdf in _BORN_DIGITAL:
        assert pdf.with_suffix(".md").exists(), (
            f"missing companion baseline for {pdf.name}; regenerate with "
            "scripts/transcode_pdf_fixtures.py"
        )


@pytest.mark.parametrize("pdf", _BORN_DIGITAL, ids=[p.stem for p in _BORN_DIGITAL])
def test_pdf_ingestion_matches_baseline(pdf: Path):
    """Re-ingesting each born-digital PDF reproduces its markdown baseline."""
    expected = pdf.with_suffix(".md").read_text(encoding="utf-8")
    assert transcode(pdf) == expected, (
        f"{pdf.name} ingestion drifted from its baseline; if the change is "
        "intended, regenerate with scripts/transcode_pdf_fixtures.py"
    )


def test_scanned_pdf_ocr_recovers_marker_tokens():
    """The synthetic image-only fixture routes through OCR and recovers text."""
    if not _OCR_FIXTURE.exists():
        pytest.skip("synthetic OCR fixture absent; run scripts/make_ocr_fixture.py")
    from duckdb_vectors.chunkers.pdf import PdfChunker

    chunker = PdfChunker()
    chunker.begin_indexing()
    try:
        chunks = list(chunker.chunk_file(_OCR_FIXTURE))
    finally:
        chunker.end_indexing()

    assert chunks, "OCR produced no chunks for the scanned fixture"
    recovered = " ".join(c.text for c in chunks).lower()
    missing = [tok for tok in _OCR_TOKENS if tok not in recovered]
    assert not missing, f"OCR did not recover expected tokens: {missing}"


@pytest.mark.parametrize("pdf", _PDFS, ids=[p.stem for p in _PDFS])
def test_pdf_chunks_carry_page_metadata(pdf: Path):
    """Every chunk records a 1-based page and a page-scoped id."""
    from duckdb_vectors.chunkers.pdf import PdfChunker

    chunker = PdfChunker()
    chunker.begin_indexing()
    try:
        chunks = list(chunker.chunk_file(pdf))
    finally:
        chunker.end_indexing()

    assert chunks, f"{pdf.name} produced no chunks"
    pages = [chunk.metadata["page"] for chunk in chunks]
    assert pages == sorted(pages), "pages must be emitted in order"
    assert min(pages) == 1
    for chunk in chunks:
        page = chunk.metadata["page"]
        local = chunk.metadata["local_pos"]
        assert chunk.chunk_id == f"{chunk.source}#p{page}:{local}"
