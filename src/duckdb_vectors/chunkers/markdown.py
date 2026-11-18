"""Markdown-aware chunking powered by the DuckDB ``markdown`` extension.

The extension parses a document into heading-scoped sections. Each section
becomes one chunk, carrying its heading breadcrumb (``section_path``) as the
title so the embedded text keeps its context. Chunk ids are stable per source
position (``{source}:{position}``) so re-indexing a document upserts in place.

Sections longer than a soft word budget are split into word windows with a
small overlap, keeping each chunk near the target size for good embedding
differentiation.

The frontmatter may carry a reserved ``sections`` mapping from a section's
``section_path`` to metadata for that section only. It is merged into the
chunks of that section, so a curator can attach a tag, such as an offline
context string, to one part of a page instead of the whole page.
"""

import logging
import os
import tempfile
from collections.abc import Iterator
from pathlib import Path

import duckdb
import yaml

from duckdb_vectors.chunkers.filesystem import FilesystemChunker
from duckdb_vectors.chunkers.windowing import split_windows
from duckdb_vectors.models import Chunk

DEFAULT_TARGET_WORDS = 300
DEFAULT_OVERLAP_WORDS = 40
MIN_CHARS = 1
FRONTMATTER_FENCE = "---"
SECTIONS_KEY = "sections"

logger = logging.getLogger(__name__)


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


def _deep_merge(base: dict, extra: dict) -> dict:
    """Merge ``extra`` into a copy of ``base``; nested mappings merge too."""
    merged = dict(base)
    for key, value in extra.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def _split_sections(frontmatter: dict, source: str) -> tuple[dict, dict[str, dict]]:
    """Take the reserved ``sections`` mapping out of the page's frontmatter.

    Returns the page-level frontmatter and ``{section_path: metadata}``. A
    ``sections`` key that is not a mapping is ordinary page metadata and is left
    in place. An entry whose value is not a mapping is ignored with a warning.
    """
    sections = frontmatter.get(SECTIONS_KEY)
    if not isinstance(sections, dict):
        return frontmatter, {}
    page = {key: value for key, value in frontmatter.items() if key != SECTIONS_KEY}
    kept: dict[str, dict] = {}
    for path, metadata in sections.items():
        if isinstance(metadata, dict):
            kept[str(path)] = metadata
        else:
            logger.warning(
                "%s: sections entry %r is not a mapping and is ignored", source, path
            )
    return page, kept


class MarkdownChunker(FilesystemChunker):
    """Split markdown into heading-scoped chunks via the DuckDB extension.

    ``target_words`` is the soft upper bound per chunk; longer sections are
    windowed with ``overlap_words`` of overlap. ``max_depth`` bounds the
    heading level that opens a new section.

    As a :class:`~duckdb_vectors.chunkers.filesystem.FilesystemChunker`, it
    claims ``text/markdown`` files during a folder walk and reads each one in
    ``chunk_file``; the text-level ``chunk`` method stays available for direct
    text input and carries the windowing logic.
    """

    mimetypes = frozenset({"text/markdown"})

    def __init__(
        self,
        target_words: int = DEFAULT_TARGET_WORDS,
        overlap_words: int = DEFAULT_OVERLAP_WORDS,
        max_depth: int = 3,
    ) -> None:
        self._target_words = target_words
        self._overlap_words = overlap_words
        self._max_depth = max_depth
        self._connection: duckdb.DuckDBPyConnection | None = None

    def _connect(self) -> duckdb.DuckDBPyConnection:
        """Return the pooled in-memory connection, loading the extension once.

        Opening a connection and loading the ``markdown`` extension cost about
        20 ms per file, roughly 85% of chunking time, so one connection is kept
        for every file in a run and closed in :meth:`end_indexing`.
        """
        if self._connection is None:
            con = duckdb.connect()
            con.execute("INSTALL markdown FROM community")
            con.execute("LOAD markdown")
            self._connection = con
        return self._connection

    def end_indexing(self) -> None:
        """Close the pooled connection at the end of an indexing run."""
        if self._connection is not None:
            self._connection.close()
            self._connection = None
        super().end_indexing()

    def _sections(self, text: str) -> list[tuple[str, str, str]]:
        """Parse into (section_path, title, body_text), in document order.

        The parser reads from a path, so the text is staged to a temp file for
        the duration of the parse. The parse runs on the pooled connection.
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
            try:
                os.remove(path)
                os.rmdir(directory)
            except OSError:
                pass
        return [(row[0], row[1], row[2].strip()) for row in rows]

    def sections(self, text: str) -> list[tuple[str, str, str]]:
        """Return ``(section_path, title, body)`` of a page, frontmatter removed.

        These are the sections :meth:`chunk` cuts into chunks, in page order,
        before windowing; the ``section_path`` is the key a ``sections`` entry
        of the frontmatter must use. Sections with an empty body are skipped.
        """
        _, body_text = _split_frontmatter(text)
        return [item for item in self._sections(body_text) if item[2]]

    def _windows(self, body: str) -> list[str]:
        """Split a long body into overlapping word windows."""
        return split_windows(body, self._target_words, self._overlap_words)

    def chunk(self, text: str, source: str) -> list[Chunk]:
        """Return heading-scoped, size-bounded chunks for one document.

        A leading YAML frontmatter block is parsed and merged into every
        chunk's metadata; the reserved ``section_path`` key always wins over
        frontmatter keys of the same name. The entry of the reserved
        ``sections`` mapping whose key is a chunk's ``section_path`` is merged
        on top, for that section's chunks only; the mapping itself is not
        copied into chunk metadata, and a key that matches no section is
        reported as a warning.
        """
        frontmatter, body_text = _split_frontmatter(text)
        page, per_section = _split_sections(frontmatter, source)
        chunks: list[Chunk] = []
        position = 0
        seen: set[str] = set()
        for section_path, title, body in self._sections(body_text):
            if not body:
                continue
            seen.add(section_path)
            base = _deep_merge(page, per_section.get(section_path, {}))
            for window in self._windows(body):
                metadata = {**base, "section_path": section_path}
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
        unknown = sorted(set(per_section) - seen)
        if unknown:
            logger.warning(
                "%s: sections entries match no section: %s", source, ", ".join(unknown)
            )
        return chunks

    def chunk_file(self, path: Path) -> Iterator[Chunk]:
        """Read a markdown file and yield its chunks.

        Reads with ``errors="replace"`` exactly as the folder indexer did, then
        delegates to the text-level ``chunk`` so output is byte-identical. The
        placeholder ``source`` given here is overwritten by
        :meth:`FilesystemChunker.chunk_folder` with the folder-relative path.
        """
        text = path.read_text(encoding="utf-8", errors="replace")
        yield from self.chunk(text, source=str(path))
