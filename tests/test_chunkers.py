"""MarkdownChunker: header splitting, stable ids, custom chunker, frontmatter."""

from pathlib import Path

import pytest

from duckdb_vectors.chunkers.filesystem import FilesystemChunker
from duckdb_vectors.chunkers.markdown import MarkdownChunker
from duckdb_vectors.chunkers.registry import register_chunker, resolve_chunker
from duckdb_vectors.models import Chunk

FIXTURES = Path(__file__).parent / "fixtures"
TREE = FIXTURES / "chunker_tree"

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


def test_markdown_default_is_filesystem_chunker():
    chunker = resolve_chunker("markdown")
    assert isinstance(chunker, FilesystemChunker)
    for name in (
        "chunk_folder",
        "chunk_file",
        "handles",
        "begin_indexing",
        "end_indexing",
        "begin_folder",
        "end_folder",
        "begin_file",
        "end_file",
    ):
        assert callable(getattr(chunker, name))


def test_handles_matches_markdown_case_insensitively():
    chunker = MarkdownChunker()
    assert chunker.handles(Path("x.md")) is True
    assert chunker.handles(Path("X.MD")) is True
    assert chunker.handles(Path("notes.txt")) is False
    assert chunker.handles(Path("README")) is False


def test_chunk_folder_recursive_covers_tree_and_skips_non_markdown():
    chunks = list(MarkdownChunker().chunk_folder(TREE, recursive=True))
    sources = {chunk.source for chunk in chunks}
    assert sources == {
        "aaa.md",
        "top.md",
        "zebra.md",
        "sub/nested.md",
        "sub/deeper/leaf.md",
    }
    assert all(not chunk.source.endswith(".txt") for chunk in chunks)
    for chunk in chunks:
        path = TREE / chunk.source
        assert chunk.source == str(path.relative_to(TREE))
        assert chunk.chunk_id == f"{chunk.source}:{chunk.position}"


def test_chunk_folder_non_recursive_yields_only_top_level():
    chunks = list(MarkdownChunker().chunk_folder(TREE, recursive=False))
    assert {chunk.source for chunk in chunks} == {"aaa.md", "top.md", "zebra.md"}


def test_chunk_file_yields_chunks_for_one_markdown_file():
    chunks = list(MarkdownChunker().chunk_file(TREE / "top.md"))
    assert chunks
    assert all(isinstance(chunk, Chunk) for chunk in chunks)


class _RecordingChunker(MarkdownChunker):
    """Records lifecycle hook calls to assert the ordering contract."""

    def __init__(self) -> None:
        super().__init__()
        self.events: list[tuple[str, str]] = []

    def begin_indexing(self) -> None:
        self.events.append(("begin_indexing", ""))

    def end_indexing(self) -> None:
        self.events.append(("end_indexing", ""))

    def begin_folder(self, path: Path) -> None:
        self.events.append(("begin_folder", path.name))

    def end_folder(self, path: Path) -> None:
        self.events.append(("end_folder", path.name))

    def begin_file(self, path: Path) -> None:
        self.events.append(("begin_file", path.name))

    def end_file(self, path: Path) -> None:
        self.events.append(("end_file", path.name))


def test_hook_ordering_contract_over_tree():
    chunker = _RecordingChunker()
    chunker.begin_indexing()
    list(chunker.chunk_folder(TREE, recursive=True))
    chunker.end_indexing()

    events = chunker.events
    assert events[0] == ("begin_indexing", "")
    assert events[-1] == ("end_indexing", "")

    folder_stack: list[str] = []
    open_file: str | None = None
    for kind, name in events[1:-1]:
        if kind == "begin_folder":
            assert open_file is None
            folder_stack.append(name)
        elif kind == "end_folder":
            assert open_file is None
            assert folder_stack and folder_stack[-1] == name
            folder_stack.pop()
        elif kind == "begin_file":
            assert folder_stack, "a file opened outside any folder"
            assert open_file is None, "files must not interleave"
            open_file = name
        elif kind == "end_file":
            assert open_file == name
            open_file = None
    assert folder_stack == []
    assert open_file is None


def test_each_folder_hook_fires_exactly_once_despite_sort_interleave():
    """A directory whose files bracket a subdirectory in sorted order must
    still open and close exactly once.

    The fixture root holds ``aaa.md`` and ``zebra.md`` on either side of the
    ``sub/`` subdirectory, so the sorted walk visits the root, descends into
    ``sub``, then returns to the root. Grouping files by parent keeps each
    directory's ``begin_folder``/``end_folder`` pair singular.
    """
    chunker = _RecordingChunker()
    list(chunker.chunk_folder(TREE, recursive=True))

    begins = [name for kind, name in chunker.events if kind == "begin_folder"]
    ends = [name for kind, name in chunker.events if kind == "end_folder"]

    # The root directory (name == TREE.name) is bracketed by aaa.md / zebra.md
    # yet must appear only once among the begin_folder events.
    assert begins.count(TREE.name) == 1
    # Every directory opens exactly as many times as it closes, and no
    # directory is opened twice.
    assert sorted(begins) == sorted(ends)
    assert len(begins) == len(set(begins))
