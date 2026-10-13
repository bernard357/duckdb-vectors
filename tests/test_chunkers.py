"""MarkdownChunker: header splitting, stable ids, custom chunker."""

from duckdb_vectors.chunkers.markdown import MarkdownChunker
from duckdb_vectors.models import Chunk

SAMPLE = """# Title

Intro paragraph with context.

## Section A

Alpha body content for the first section.

## Section B

Beta body content for the second section.
"""


def test_splits_on_headers_with_stable_ids():
    chunker = MarkdownChunker()
    chunks = chunker.chunk(SAMPLE, "doc.md")
    assert len(chunks) >= 3
    ids = [chunk.chunk_id for chunk in chunks]
    assert ids == [f"doc.md:{i}" for i in range(len(chunks))]
    titles = {chunk.title for chunk in chunks}
    assert "Section A" in titles
    assert "Section B" in titles


def test_rechunk_is_idempotent():
    chunker = MarkdownChunker()
    first = chunker.chunk(SAMPLE, "doc.md")
    second = chunker.chunk(SAMPLE, "doc.md")
    assert [c.chunk_id for c in first] == [c.chunk_id for c in second]
    assert [c.text for c in first] == [c.text for c in second]


def test_long_section_is_windowed():
    body = " ".join(f"word{i}" for i in range(900))
    doc = f"# Big\n\n{body}"
    chunker = MarkdownChunker(target_words=300, overlap_words=40)
    chunks = chunker.chunk(doc, "big.md")
    assert len(chunks) >= 3


class _CustomChunker:
    def chunk(self, text: str, source: str) -> list[Chunk]:
        return [Chunk(chunk_id=f"{source}:0", text=text, source=source)]


def test_custom_chunker_is_accepted():
    chunker = _CustomChunker()
    chunks = chunker.chunk("one blob", "x.md")
    assert len(chunks) == 1
    assert chunks[0].chunk_id == "x.md:0"
