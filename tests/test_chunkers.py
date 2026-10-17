"""MarkdownChunker: header splitting, stable ids, custom chunker, frontmatter."""

from pathlib import Path

import pytest

from duckdb_vectors.chunkers.markdown import MarkdownChunker
from duckdb_vectors.chunkers.registry import register_chunker, resolve_chunker
from duckdb_vectors.models import Chunk

FIXTURES = Path(__file__).parent / "fixtures"

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


def test_frontmatter_keys_merge_into_every_chunk():
    text = (FIXTURES / "with_frontmatter.md").read_text(encoding="utf-8")
    chunks = MarkdownChunker().chunk(text, "policy.md")
    assert len(chunks) >= 2
    for chunk in chunks:
        assert chunk.metadata["kind"] == "policy"
        assert chunk.metadata["owner"] == "alice"
        assert chunk.metadata["tags"] == ["security", "access"]
        assert "section_path" in chunk.metadata


def test_frontmatter_does_not_clobber_section_path():
    text = "---\nsection_path: injected\n---\n# Title\n\nBody text here.\n"
    chunks = MarkdownChunker().chunk(text, "doc.md")
    assert chunks
    for chunk in chunks:
        assert chunk.metadata["section_path"] != "injected"


def test_no_frontmatter_yields_only_section_path():
    chunks = MarkdownChunker().chunk(SAMPLE, "doc.md")
    assert chunks
    for chunk in chunks:
        assert set(chunk.metadata) == {"section_path"}


def test_malformed_frontmatter_does_not_raise():
    text = "---\n: : :\n---\n# Title\n\nbody\n"
    chunks = MarkdownChunker().chunk(text, "doc.md")
    assert chunks
    for chunk in chunks:
        assert set(chunk.metadata) == {"section_path"}


def test_registry_round_trip():
    register_chunker("dummy", lambda variant: _CustomChunker())
    chunker = resolve_chunker("dummy")
    assert isinstance(chunker, _CustomChunker)


def test_registry_raises_on_unknown():
    with pytest.raises(ValueError, match="Unknown chunker") as excinfo:
        resolve_chunker("nope")
    assert "markdown" in str(excinfo.value)


def test_registry_resolves_markdown_default():
    chunker = resolve_chunker("markdown")
    assert isinstance(chunker, MarkdownChunker)
