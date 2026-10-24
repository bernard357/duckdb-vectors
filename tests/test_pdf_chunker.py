"""PdfChunker: page-aware chunks, OCR fallback, threshold routing, lifecycle.

All tests run fully offline — the RapidOCR engine is always stubbed, so no OCR
model is ever downloaded or invoked. Fixtures are tiny PDFs built at test time
with pypdf (born-digital pages carry a real text layer; scanned pages carry
none), so no PDF binaries are committed.
"""

from pathlib import Path

import pytest
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from duckdb_vectors.chunkers.pdf import PdfChunker
from duckdb_vectors.chunkers.registry import resolve_chunker


def _add_text_page(writer: PdfWriter, words: str) -> None:
    """Append a page carrying ``words`` as a real embedded text layer."""
    page = writer.add_blank_page(width=612, height=792)
    font = DictionaryObject()
    font[NameObject("/Type")] = NameObject("/Font")
    font[NameObject("/Subtype")] = NameObject("/Type1")
    font[NameObject("/BaseFont")] = NameObject("/Helvetica")
    resources = DictionaryObject()
    fonts = DictionaryObject()
    fonts[NameObject("/F1")] = writer._add_object(font)
    resources[NameObject("/Font")] = fonts
    page[NameObject("/Resources")] = resources
    stream = DecodedStreamObject()
    stream.set_data(f"BT /F1 24 Tf 72 700 Td ({words}) Tj ET".encode("latin-1"))
    page[NameObject("/Contents")] = writer._add_object(stream)


def _write_born_digital(path: Path, pages: list[str]) -> Path:
    writer = PdfWriter()
    for words in pages:
        _add_text_page(writer, words)
    with open(path, "wb") as handle:
        writer.write(handle)
    return path


def _write_scanned(path: Path, page_count: int = 1) -> Path:
    """Write blank, text-layer-free pages to force the OCR branch."""
    writer = PdfWriter()
    for _ in range(page_count):
        writer.add_blank_page(width=612, height=792)
    with open(path, "wb") as handle:
        writer.write(handle)
    return path


class _StubEngine:
    """Stand in for RapidOCR: returns canned lines, counts calls."""

    def __init__(self, lines: list[str] | None = None) -> None:
        self.lines = lines if lines is not None else ["scanned text recovered"]
        self.calls = 0

    def __call__(self, image):
        self.calls += 1
        result = [[None, line, 0.99] for line in self.lines]
        return result, 0.0


def test_handles_matches_pdf_case_insensitively():
    chunker = PdfChunker()
    assert chunker.handles(Path("doc.pdf")) is True
    assert chunker.handles(Path("DOC.PDF")) is True
    assert chunker.handles(Path("notes.md")) is False
    assert chunker.handles(Path("notes.txt")) is False


def test_resolve_chunker_returns_pdf_chunker():
    chunker = resolve_chunker("pdf")
    assert isinstance(chunker, PdfChunker)


def test_born_digital_pages_in_order_with_page_scoped_ids(tmp_path):
    pdf = _write_born_digital(
        tmp_path / "born.pdf",
        ["Alpha content for the first page", "Beta content for the second page"],
    )
    chunker = PdfChunker()
    chunker._engine = _StubEngine()  # must not be used for born-digital text
    chunks = list(chunker.chunk_folder(tmp_path, recursive=False))

    assert [c.metadata["page"] for c in chunks] == [1, 2]
    source = pdf.name
    assert [c.source for c in chunks] == [source, source]
    assert [c.chunk_id for c in chunks] == [
        f"{source}#p1:0",
        f"{source}#p2:0",
    ]
    assert [c.position for c in chunks] == [0, 1]
    assert [c.title for c in chunks] == ["Page 1", "Page 2"]
    assert "Alpha" in chunks[0].text
    assert "Beta" in chunks[1].text
    assert chunker._engine.calls == 0


def test_scanned_page_routes_through_ocr(tmp_path):
    pdf = _write_scanned(tmp_path / "scanned.pdf", page_count=1)
    stub = _StubEngine(["recognized line one", "recognized line two"])
    chunker = PdfChunker()
    chunker._engine = stub
    chunks = list(chunker.chunk_file(pdf))

    assert stub.calls == 1
    assert len(chunks) == 1
    assert chunks[0].metadata["page"] == 1
    assert "recognized line one" in chunks[0].text
    assert "recognized line two" in chunks[0].text


def test_threshold_routes_below_min_chars_to_ocr(tmp_path):
    pdf = _write_born_digital(tmp_path / "short.pdf", ["hi"])
    stub = _StubEngine(["ocr text"])
    chunker = PdfChunker(min_chars=16)
    chunker._engine = stub
    chunks = list(chunker.chunk_file(pdf))

    assert stub.calls == 1
    assert "ocr text" in chunks[0].text


def test_threshold_keeps_embedded_text_above_min_chars(tmp_path):
    pdf = _write_born_digital(
        tmp_path / "long.pdf", ["plenty of real embedded words here"]
    )
    stub = _StubEngine(["should not appear"])
    chunker = PdfChunker(min_chars=16)
    chunker._engine = stub
    chunks = list(chunker.chunk_file(pdf))

    assert stub.calls == 0
    assert "embedded" in chunks[0].text
    assert "should not appear" not in chunks[0].text


def test_engine_built_once_and_released(monkeypatch):
    built = {"count": 0}

    class _Counting:
        def __init__(self, *args, **kwargs):
            built["count"] += 1

        def __call__(self, image):  # pragma: no cover - not called here
            return None, 0.0

    import duckdb_vectors.chunkers.pdf as pdf_module

    monkeypatch.setattr("rapidocr_onnxruntime.RapidOCR", _Counting, raising=False)
    chunker = PdfChunker()
    assert chunker._engine is None

    chunker.begin_indexing()
    chunker.begin_indexing()  # idempotent — no rebuild
    assert built["count"] == 1
    assert chunker._engine is not None

    chunker.end_indexing()
    assert chunker._engine is None
    assert pdf_module  # module import kept for monkeypatch target clarity


def test_engine_reused_across_multiple_files(monkeypatch):
    built = {"count": 0}

    class _Counting:
        def __init__(self, *args, **kwargs):
            built["count"] += 1

        def __call__(self, image):  # pragma: no cover - not exercised
            return None, 0.0

    monkeypatch.setattr("rapidocr_onnxruntime.RapidOCR", _Counting, raising=False)
    chunker = PdfChunker()
    chunker.begin_indexing()
    engine = chunker._engine
    chunker.begin_indexing()
    assert chunker._engine is engine
    assert built["count"] == 1


def test_chunk_text_is_not_supported():
    with pytest.raises(NotImplementedError):
        PdfChunker().chunk("text", "doc.pdf")
