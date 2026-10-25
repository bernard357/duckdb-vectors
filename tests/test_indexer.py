"""Folder indexer: all-embed, zero-reembed, single-change, prune."""

import shutil
from pathlib import Path

from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from duckdb_vectors.chunkers.composite import CompositeChunker
from duckdb_vectors.chunkers.pdf import PdfChunker
from duckdb_vectors.indexer import index_folder
from duckdb_vectors.store import VectorStore

FIXTURES = Path(__file__).parent / "fixtures"
TREE = FIXTURES / "chunker_tree"

DOC_A = "# Alpha\n\nAlpha body content here.\n\n## Sub\n\nSub body text.\n"
DOC_B = "# Beta\n\nBeta body content here.\n"


def _store(tmp_path):
    return VectorStore(db_path=str(tmp_path / "idx.duckdb"), backend="hashing")


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


def test_first_run_embeds_all(tmp_path):
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "a.md").write_text(DOC_A)
    (docs / "b.md").write_text(DOC_B)
    store = _store(tmp_path)
    report = index_folder(store, docs)
    assert report.files == 2
    assert report.chunks >= 3
    assert report.embedded == report.chunks
    assert report.reused == 0
    store.close()


def test_second_run_reuses_everything(tmp_path):
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "a.md").write_text(DOC_A)
    store = _store(tmp_path)
    index_folder(store, docs)
    report = index_folder(store, docs)
    assert report.embedded == 0
    assert report.reused == report.chunks
    store.close()


def test_editing_one_file_reembeds_only_its_chunks(tmp_path):
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "a.md").write_text(DOC_A)
    (docs / "b.md").write_text(DOC_B)
    store = _store(tmp_path)
    index_folder(store, docs)
    (docs / "b.md").write_text("# Beta\n\nCompletely new beta content.\n")
    report = index_folder(store, docs)
    assert report.embedded >= 1
    assert report.reused >= 1
    store.close()


def test_frontmatter_flows_through_query(tmp_path):
    docs = tmp_path / "docs"
    docs.mkdir()
    shutil.copy(FIXTURES / "with_frontmatter.md", docs / "policy.md")
    store = _store(tmp_path)
    index_folder(store, docs)
    results = store.query("authenticate restricted documents", top_k=3)
    assert results
    hit = results[0]
    assert hit.chunk.metadata["kind"] == "policy"
    assert hit.chunk.metadata["owner"] == "alice"
    assert "section_path" in hit.chunk.metadata
    store.close()


def test_deleted_file_is_pruned(tmp_path):
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "a.md").write_text(DOC_A)
    (docs / "b.md").write_text(DOC_B)
    store = _store(tmp_path)
    index_folder(store, docs)
    before = store.count()
    (docs / "b.md").unlink()
    report = index_folder(store, docs)
    assert report.pruned >= 1
    assert store.count() < before
    store.close()


def _stored_rows(store):
    return store._con.execute(
        "SELECT chunk_id, source, position, title FROM chunks ORDER BY chunk_id"
    ).fetchall()


def test_markdown_tree_indexing_is_stable(tmp_path):
    """Byte-identical regression guard: chunker-driven walk over a nested tree.

    Locks the concrete chunk ids and sources the folder walk produces, then
    exercises re-index reuse, single-file re-embed, and prune so the refactor
    cannot silently change indexing behavior.
    """
    docs = tmp_path / "docs"
    shutil.copytree(TREE, docs)
    store = _store(tmp_path)

    report = index_folder(store, docs, recursive=True)
    assert report.files == 5
    rows = _stored_rows(store)
    ids = [row[0] for row in rows]
    sources = [row[1] for row in rows]
    assert ids == [
        "aaa.md:0",
        "sub/deeper/leaf.md:0",
        "sub/nested.md:0",
        "top.md:0",
        "zebra.md:0",
    ]
    assert sources == [
        "aaa.md",
        "sub/deeper/leaf.md",
        "sub/nested.md",
        "top.md",
        "zebra.md",
    ]
    assert all(row[2] == 0 for row in rows)
    assert report.chunks == 5
    assert report.embedded == report.chunks
    assert report.reused == 0

    rerun = index_folder(store, docs, recursive=True)
    assert rerun.files == 5
    assert rerun.chunks == 5
    assert rerun.embedded == 0
    assert rerun.reused == rerun.chunks
    assert _stored_rows(store) == rows

    (docs / "sub" / "nested.md").write_text(
        "# Nested\n\nCompletely new nested body content here.\n"
    )
    edited = index_folder(store, docs, recursive=True)
    assert edited.embedded >= 1
    assert edited.reused >= 1

    (docs / "sub" / "deeper" / "leaf.md").unlink()
    pruned = index_folder(store, docs, recursive=True)
    assert pruned.files == 4
    assert pruned.pruned >= 1
    remaining = {row[1] for row in _stored_rows(store)}
    assert "sub/deeper/leaf.md" not in remaining
    store.close()


def test_default_chunker_is_composite_over_mixed_folder(tmp_path, monkeypatch):
    """With no explicit ``chunker``, ``index_folder`` dispatches through the
    composite chunker, indexing both a ``.md`` and a ``.pdf`` file in one pass.

    The PDF members' OCR engine is stubbed so born-digital pages never download
    a model; the mixed folder carries one markdown file and one born-digital
    PDF.
    """
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "a.md").write_text(DOC_A)
    _write_born_digital(
        docs / "b.pdf",
        ["Alpha content for the first page", "Beta content for the second page"],
    )

    original = CompositeChunker.begin_indexing

    def _stub_begin(self) -> None:
        for member in self._chunkers:
            if isinstance(member, PdfChunker):
                member._engine = _StubEngine()
        original(self)

    monkeypatch.setattr(CompositeChunker, "begin_indexing", _stub_begin)

    store = _store(tmp_path)
    report = index_folder(store, docs, recursive=True)
    sources = {row[1] for row in _stored_rows(store)}
    assert "a.md" in sources
    assert "b.pdf" in sources
    assert report.files == 2
    store.close()
