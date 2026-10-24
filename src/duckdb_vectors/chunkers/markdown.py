"""Markdown-aware chunking powered by the DuckDB ``markdown`` extension.

The extension parses a document into heading-scoped sections. Each section
becomes one chunk, carrying its heading breadcrumb (``section_path``) as the
title so the embedded text keeps its context. Chunk ids are stable per source
position (``{source}:{position}``) so re-indexing a document upserts in place.

Sections longer than a soft word budget are split into word windows with a
small overlap, keeping each chunk near the target size for good embedding
differentiation.
"""

import os
import tempfile
from collections.abc import Iterator
from pathlib import Path

import duckdb
import yaml

from duckdb_vectors.chunkers.filesystem import FilesystemChunker
from duckdb_vectors.models import Chunk

DEFAULT_TARGET_WORDS = 300
DEFAULT_OVERLAP_WORDS = 40
MIN_CHARS = 1
FRONTMATTER_FENCE = "---"


def _split_frontmatter(text: str) -> tuple[dict, str]:
    """Split a leading YAML frontmatter block from the body.

    Returns the parsed mapping and the body with the block stripped. If there
    is no block, the mapping is empty and the body is the original text. A
    block that fails to parse or does not yield a mapping is treated as
    ordinary body text (empty mapping, original text) so one malformed page
    never breaks indexing.
    """
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].rstrip("\r\n") != FRONTMATTER_FENCE:
        return {}, text
    for index in range(1, len(lines)):
        if lines[index].rstrip("\r\n") != FRONTMATTER_FENCE:
            continue
        block = "".join(lines[1:index])
        try:
            parsed = yaml.safe_load(block)
        except yaml.YAMLError:
            return {}, text
        if not isinstance(parsed, dict):
            return {}, text
        return parsed, "".join(lines[index + 1 :])
    return {}, text


class MarkdownChunker(FilesystemChunker):
    """Split markdown into heading-scoped chunks via the DuckDB extension.

    ``target_words`` is the soft upper bound per chunk; longer sections are
    windowed with ``overlap_words`` of overlap. ``max_depth`` bounds the
    heading level that opens a new section.

    As a :class:`~duckdb_vectors.chunkers.filesystem.FilesystemChunker`, it
    claims ``*.md`` files during a folder walk and reads each one in
    ``chunk_file``; the text-level ``chunk`` method stays available for direct
    text input and carries the windowing logic.
    """

    def __init__(
        self,
        target_words: int = DEFAULT_TARGET_WORDS,
        overlap_words: int = DEFAULT_OVERLAP_WORDS,
        max_depth: int = 3,
    ) -> None:
        self._target_words = target_words
        self._overlap_words = overlap_words
        self._max_depth = max_depth

    def _connect(self) -> duckdb.DuckDBPyConnection:
        con = duckdb.connect()
        con.execute("INSTALL markdown FROM community")
        con.execute("LOAD markdown")
        return con

    def _sections(self, text: str) -> list[tuple[str, str, str]]:
        """Parse into (section_path, title, body_text), in document order.

        The parser reads from a path, so the text is staged to a temp file for
        the duration of the parse.
        """
        con = self._connect()
        directory = tempfile.mkdtemp(prefix="duckdb_vectors_")
        path = os.path.join(directory, "document.md")
        try:
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(text)
            rows = con.execute(
                """
                SELECT COALESCE(section_path, '') AS section_path,
                       COALESCE(title, '') AS title,
                       md_to_text(content) AS body
                FROM read_markdown_sections(?)
                WHERE level >= 1
                  AND level <= ?
                  AND length(md_to_text(content)) >= ?
                ORDER BY start_line
                """,
                [path, self._max_depth, MIN_CHARS],
            ).fetchall()
        finally:
            con.close()
            try:
                os.remove(path)
                os.rmdir(directory)
            except OSError:
                pass
        return [(row[0], row[1], row[2].strip()) for row in rows]

    def _windows(self, body: str) -> list[str]:
        """Split a long body into overlapping word windows."""
        words = body.split()
        if len(words) <= self._target_words:
            return [body]
        step = max(1, self._target_words - self._overlap_words)
        windows = []
        for start in range(0, len(words), step):
            window = words[start : start + self._target_words]
            if window:
                windows.append(" ".join(window))
            if start + self._target_words >= len(words):
                break
        return windows

    def chunk(self, text: str, source: str) -> list[Chunk]:
        """Return heading-scoped, size-bounded chunks for one document.

        A leading YAML frontmatter block is parsed and merged into every
        chunk's metadata; the reserved ``section_path`` key always wins over
        frontmatter keys of the same name.
        """
        frontmatter, body_text = _split_frontmatter(text)
        chunks: list[Chunk] = []
        position = 0
        for section_path, title, body in self._sections(body_text):
            if not body:
                continue
            for window in self._windows(body):
                metadata = {**frontmatter, "section_path": section_path}
                chunks.append(
                    Chunk(
                        chunk_id=f"{source}:{position}",
                        text=window,
                        source=source,
                        position=position,
                        title=title or section_path,
                        metadata=metadata,
                    )
                )
                position += 1
        return chunks

    def handles(self, path: Path) -> bool:
        """Claim files with a ``.md`` suffix, case-insensitively."""
        return path.suffix.lower() == ".md"

    def chunk_file(self, path: Path) -> Iterator[Chunk]:
        """Read a markdown file and yield its chunks.

        Reads with ``errors="replace"`` exactly as the folder indexer did, then
        delegates to the text-level ``chunk`` so output is byte-identical. The
        placeholder ``source`` given here is overwritten by
        :meth:`FilesystemChunker.chunk_folder` with the folder-relative path.
        """
        text = path.read_text(encoding="utf-8", errors="replace")
        yield from self.chunk(text, source=str(path))
