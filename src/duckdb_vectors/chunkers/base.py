"""The chunker protocol every splitter implements.

A chunker owns both the filesystem traversal of a folder and the splitting of
each document it claims. The indexer drives a chunker through three nested
begin/end hook pairs so a chunker can set up and tear down resources at the
right granularity:

- ``begin_indexing`` / ``end_indexing`` wrap a whole :func:`index_folder` run —
  the place to acquire or release an expensive, run-scoped resource once (for
  example a local OCR engine).
- ``begin_folder`` / ``end_folder`` wrap each directory the walk enters.
- ``begin_file`` / ``end_file`` wrap each file the chunker handles.

For one run the hooks fire in a strict nesting order::

    begin_indexing
      begin_folder(dir)
        begin_file(f) ... yield chunks of f ... end_file(f)
        ...
      end_folder(dir)
      ...
    end_indexing

Every ``begin_*`` is matched by its ``end_*``, and ``end_indexing`` always runs
even when indexing raises.

The lifecycle hooks have no-op defaults, so a minimal chunker only needs to
implement ``handles`` plus ``chunk_file`` (traversal and hooks come from
:class:`~duckdb_vectors.chunkers.filesystem.FilesystemChunker`), or just the
text-level ``chunk`` method for direct text input.
"""

from collections.abc import Iterator
from pathlib import Path
from typing import Protocol, runtime_checkable

from duckdb_vectors.models import Chunk


@runtime_checkable
class Chunker(Protocol):
    """Split a source document into ordered, indexable chunks.

    ``source`` is a stable identifier for the document (typically a relative
    file path). Implementations assign a stable ``chunk_id`` per position so a
    re-index of the same source upserts rather than duplicates.
    """

    def chunk(self, text: str, source: str) -> list[Chunk]:
        """Return the chunks for one document, in document order."""
        ...

    def handles(self, path: Path) -> bool:
        """Return whether this chunker claims ``path`` during a folder walk."""
        ...

    def chunk_folder(self, folder: Path, recursive: bool = True) -> Iterator[Chunk]:
        """Walk ``folder`` and yield chunks for every handled file.

        Fires the folder and file lifecycle hooks around the walk and sets each
        chunk's ``source`` to its path relative to ``folder``.
        """
        ...

    def chunk_file(self, path: Path) -> Iterator[Chunk]:
        """Read ``path`` and yield its chunks, in document order."""
        ...

    def begin_indexing(self) -> None:
        """Run once before a whole indexing pass. No-op by default."""
        ...

    def end_indexing(self) -> None:
        """Run once after a whole indexing pass, even on error. No-op."""
        ...

    def begin_folder(self, path: Path) -> None:
        """Run when the walk enters a directory. No-op by default."""
        ...

    def end_folder(self, path: Path) -> None:
        """Run when the walk leaves a directory. No-op by default."""
        ...

    def begin_file(self, path: Path) -> None:
        """Run before a handled file is chunked. No-op by default."""
        ...

    def end_file(self, path: Path) -> None:
        """Run after a handled file is chunked. No-op by default."""
        ...
