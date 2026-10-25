"""A dispatching chunker that indexes a mixed-type folder in one pass.

:class:`CompositeChunker` holds an ordered list of sub-chunker instances and
routes each file to the first one that claims it. It reuses the whole walk,
hook firing, and ``source`` assignment from
:class:`~duckdb_vectors.chunkers.filesystem.FilesystemChunker`; it overrides
only ``handles``, ``chunk_file``, ``_chunk_id``, ``begin_indexing``, and
``end_indexing``.

Ids stay byte-identical to each file's own chunker: the walk assigns the same
relative ``source`` and re-derives each id through :meth:`_chunk_id`, which the
composite delegates to the active sub-chunker's own id scheme (markdown
``{source}:{position}``, PDF ``{source}#p{page}:{local_pos}``). The base
processes one file fully before the next, so ``self._active`` is always the
sub-chunker that produced the chunk being id'd.
"""

from collections.abc import Iterator
from pathlib import Path

from duckdb_vectors.chunkers.filesystem import FilesystemChunker
from duckdb_vectors.models import Chunk


class CompositeChunker(FilesystemChunker):
    """Dispatch each handled file to the first sub-chunker that claims it.

    Members are tried in order; the first whose ``handles`` returns True owns
    the file. ``begin_indexing`` / ``end_indexing`` fan out to every member so
    run-scoped resources (for example the PDF OCR engine) are set up and torn
    down once per run; teardown is best-effort so one failing member does not
    skip the others.
    """

    def __init__(self, chunkers: list[FilesystemChunker]) -> None:
        self._chunkers = chunkers
        self._active: FilesystemChunker | None = None

    def handles(self, path: Path) -> bool:
        """Claim ``path`` if any sub-chunker claims it."""
        return any(chunker.handles(path) for chunker in self._chunkers)

    def chunk_file(self, path: Path) -> Iterator[Chunk]:
        """Dispatch ``path`` to the first sub-chunker that claims it.

        Records the chosen sub-chunker as ``self._active`` so :meth:`_chunk_id`
        keeps that file on the sub-chunker's own id scheme. If no sub-chunker
        claims the file, resets ``self._active`` and yields nothing.
        """
        self._active = None
        for chunker in self._chunkers:
            if chunker.handles(path):
                self._active = chunker
                yield from chunker.chunk_file(path)
                return

    def _chunk_id(self, source: str, chunk: Chunk) -> str:
        """Delegate id derivation to the active sub-chunker's scheme."""
        if self._active is None:  # pragma: no cover - never on the walk's path
            raise RuntimeError("_chunk_id called with no active sub-chunker")
        return self._active._chunk_id(source, chunk)

    def begin_indexing(self) -> None:
        """Fan ``begin_indexing`` out to every sub-chunker, in order."""
        for chunker in self._chunkers:
            chunker.begin_indexing()

    def end_indexing(self) -> None:
        """Fan ``end_indexing`` out to every sub-chunker, best-effort.

        Each member's teardown is attempted even if an earlier one raised; the
        first exception is re-raised after all members have been attempted.
        """
        first_error: Exception | None = None
        for chunker in self._chunkers:
            try:
                chunker.end_indexing()
            except Exception as exc:  # noqa: BLE001 - best-effort teardown
                if first_error is None:
                    first_error = exc
        if first_error is not None:
            raise first_error
