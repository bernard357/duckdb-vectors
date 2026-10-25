"""CompositeChunker: mixed-folder dispatch, byte-identical ids, fan-out hooks.

All tests run fully offline — the RapidOCR engine is always stubbed, so no OCR
model is ever downloaded or invoked. The mixed folder is built at test time
(one born-digital ``.pdf`` carrying a real text layer, one inline ``.md``), so
no binaries are committed.
"""

from pathlib import Path

from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from duckdb_vectors.chunkers.composite import CompositeChunker
from duckdb_vectors.chunkers.filesystem import FilesystemChunker
from duckdb_vectors.chunkers.markdown import MarkdownChunker
from duckdb_vectors.chunkers.pdf import PdfChunker
from duckdb_vectors.chunkers.registry import resolve_chunker

SAMPLE_MD = """# Title

Intro paragraph with context.

## Section A

Alpha body content for the first section.

## Section B

Beta body content for the second section.
"""


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


class _StubEngine:
    """Stand in for RapidOCR so born-digital tests never touch a model."""

    def __call__(self, image):  # pragma: no cover - born-digital never OCRs
        return [], 0.0


def _build_mixed_folder(root: Path) -> None:
    (root / "a.md").write_text(SAMPLE_MD, encoding="utf-8")
    _write_born_digital(
        root / "b.pdf",
        ["Alpha content for the first page", "Beta content for the second page"],
    )


def _stub_pdf_members(chunker: CompositeChunker) -> None:
    """Assign a stub OCR engine to every PDF member of the composite."""
    for member in chunker._chunkers:
        if isinstance(member, PdfChunker):
            member._engine = _StubEngine()


def test_mixed_folder_yields_both_types_in_one_pass(tmp_path):
    _build_mixed_folder(tmp_path)
    composite = resolve_chunker("composite")
    assert isinstance(composite, CompositeChunker)
    _stub_pdf_members(composite)

    composite.begin_indexing()
    chunks = list(composite.chunk_folder(tmp_path, recursive=True))
    composite.end_indexing()

    sources = {chunk.source for chunk in chunks}
    assert "a.md" in sources
    assert "b.pdf" in sources


def test_ids_are_byte_identical_to_each_own_chunker(tmp_path):
    _build_mixed_folder(tmp_path)

    composite = resolve_chunker("composite")
    assert isinstance(composite, CompositeChunker)
    _stub_pdf_members(composite)
    composite.begin_indexing()
    composite_ids: dict[str, list[str]] = {}
    for chunk in composite.chunk_folder(tmp_path, recursive=True):
        composite_ids.setdefault(chunk.source, []).append(chunk.chunk_id)
    composite.end_indexing()

    markdown = MarkdownChunker()
    md_ids: dict[str, list[str]] = {}
    for chunk in markdown.chunk_folder(tmp_path, recursive=True):
        md_ids.setdefault(chunk.source, []).append(chunk.chunk_id)

    pdf = PdfChunker()
    pdf._engine = _StubEngine()
    pdf_ids: dict[str, list[str]] = {}
    for chunk in pdf.chunk_folder(tmp_path, recursive=True):
        pdf_ids.setdefault(chunk.source, []).append(chunk.chunk_id)

    expected = {**md_ids, **pdf_ids}
    assert composite_ids == expected
    assert md_ids["a.md"] == [f"a.md:{i}" for i in range(len(md_ids["a.md"]))]
    assert pdf_ids["b.pdf"] == ["b.pdf#p1:0", "b.pdf#p2:0"]


def test_handles_true_for_known_false_for_unknown():
    composite = resolve_chunker("composite")
    assert composite.handles(Path("x.md")) is True
    assert composite.handles(Path("y.pdf")) is True
    assert composite.handles(Path("z.txt")) is False


def test_first_claimer_dispatch_is_deterministic(tmp_path):
    note = tmp_path / "notes.md"
    note.write_text(SAMPLE_MD, encoding="utf-8")
    markdown = MarkdownChunker()
    pdf = PdfChunker()
    composite = CompositeChunker([markdown, pdf])

    list(composite.chunk_file(note))
    assert composite._active is markdown

    composite._active = None
    list(composite.chunk_file(tmp_path / "unhandled.txt"))
    assert composite._active is None


def test_begin_indexing_fans_out_to_every_member():
    class _Recording(FilesystemChunker):
        def __init__(self) -> None:
            self.began = False

        def begin_indexing(self) -> None:
            self.began = True

    recording = _Recording()
    pdf = PdfChunker()
    pdf._engine = _StubEngine()
    composite = CompositeChunker([recording, pdf])

    composite.begin_indexing()
    assert recording.began is True
    assert pdf._engine is not None


def test_end_indexing_fans_out_even_when_one_member_raises():
    class _Raising(FilesystemChunker):
        def end_indexing(self) -> None:
            raise RuntimeError("teardown failed")

    class _Recording(FilesystemChunker):
        def __init__(self) -> None:
            self.ended = False

        def end_indexing(self) -> None:
            self.ended = True

    raising = _Raising()
    recording = _Recording()
    composite = CompositeChunker([raising, recording])

    import pytest

    with pytest.raises(RuntimeError, match="teardown failed"):
        composite.end_indexing()
    assert recording.ended is True
