"""A filesystem-walking base for format chunkers.

:class:`FilesystemChunker` owns the recursive folder walk and the lifecycle
hooks described on :class:`~duckdb_vectors.chunkers.base.Chunker`. A concrete
chunker subclasses it and implements only two methods:

- ``handles(path)`` — which files this chunker claims (for example ``*.md``).
- ``chunk_file(path)`` — read one file and yield its chunks.

The base provides no-op implementations of all six lifecycle hooks, so a
subclass overrides only the ones it needs. ``begin_indexing`` / ``end_indexing``
are the natural place to load and release a run-scoped resource once (for
example a local OCR engine for a future PDF chunker).
"""

from collections.abc import Iterator
from pathlib import Path

from duckdb_vectors.models import Chunk


class FilesystemChunker:
    """Walk a folder, claim files via ``handles``, and chunk them.

    Subclasses implement ``handles`` and ``chunk_file``; everything else —
    traversal order, folder/file hook firing, and relative-``source``
    assignment — is handled here so every format chunker behaves identically.

    ``_files_seen`` records how many handled files the last ``chunk_folder``
    walk visited, including files that yielded no chunks, so the indexer can
    report a file count identical to the previous markdown-only walk.
    """

    _files_seen: int = 0

    def handles(self, path: Path) -> bool:
        """Return whether this chunker claims ``path``. Override in subclasses."""
        return False

    def chunk_file(self, path: Path) -> Iterator[Chunk]:
        """Read ``path`` and yield its chunks. Override in subclasses."""
        raise NotImplementedError
        yield  # pragma: no cover - marks this a generator for typing

    def chunk(self, text: str, source: str) -> list[Chunk]:
        """Return the chunks for one document from text. Override if supported."""
        raise NotImplementedError

    def begin_indexing(self) -> None:
        """Run once before a whole indexing pass. No-op by default."""

    def end_indexing(self) -> None:
        """Run once after a whole indexing pass, even on error. No-op."""

    def begin_folder(self, path: Path) -> None:
        """Run when the walk enters a directory. No-op by default."""

    def end_folder(self, path: Path) -> None:
        """Run when the walk leaves a directory. No-op by default."""

    def begin_file(self, path: Path) -> None:
        """Run before a handled file is chunked. No-op by default."""

    def end_file(self, path: Path) -> None:
        """Run after a handled file is chunked. No-op by default."""

    def _chunk_id(self, source: str, chunk: Chunk) -> str:
        """Return the stable id for ``chunk`` within ``source``.

        The base owns ``source`` assignment; subclasses may override only the
        id *format*. The default is ``{source}:{position}``.
        """
        return f"{source}:{chunk.position}"

    def chunk_folder(self, folder: Path, recursive: bool = True) -> Iterator[Chunk]:
        """Walk ``folder`` and yield chunks for every handled file.

        Files are visited in sorted path order — the same order the indexer used
        before traversal moved here. The folder hooks fire once per directory
        that contains at least one handled file, in first-appearance order;
        ``begin_folder`` precedes the first file of a directory and
        ``end_folder`` follows its last. Each yielded chunk's ``source`` is set
        to the file's path relative to ``folder`` (``str(path.relative_to(...))``)
        and its ``chunk_id`` is re-derived through :meth:`_chunk_id` (default
        ``{source}:{position}``) so ids and sources are owned here and stay
        stable across runs regardless of the placeholder source ``chunk_file``
        used.
        """
        folder = Path(folder)
        pattern = "**/*" if recursive else "*"
        candidates = [
            path
            for path in sorted(folder.glob(pattern))
            if path.is_file() and self.handles(path)
        ]
        # Group handled files by parent directory so each directory's hooks
        # fire exactly once, even when lexical sort order interleaves a
        # subdirectory's files between a parent's (e.g. "a/b/d.md" sorts before
        # "a/c.md"). Directories keep first-appearance order; files keep their
        # sorted order within each directory.
        groups: dict[Path, list[Path]] = {}
        for path in candidates:
            groups.setdefault(path.parent, []).append(path)
        self._files_seen = 0
        for parent, paths in groups.items():
            self.begin_folder(parent)
            for path in paths:
                self._files_seen += 1
                self.begin_file(path)
                source = str(path.relative_to(folder))
                for chunk in self.chunk_file(path):
                    chunk.source = source
                    chunk.chunk_id = self._chunk_id(source, chunk)
                    yield chunk
                self.end_file(path)
            self.end_folder(parent)
