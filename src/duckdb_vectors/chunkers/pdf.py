"""Page-aware PDF chunking with a local OCR fallback for scanned pages.

A PDF is read one page at a time. A born-digital page carries an embedded text
layer, which :mod:`pypdf` extracts directly. A scanned page has no usable text
layer — its text sits inside a rasterised image — so when a page's embedded
text is shorter than ``min_chars`` the page is rendered to a bitmap with
:mod:`pypdfium2` and read with a local RapidOCR engine. Either way the page's
text is split into the same overlapping word windows the markdown chunker uses,
and one :class:`~duckdb_vectors.models.Chunk` is emitted per window.

Each chunk records its 1-based ``page`` in ``metadata`` and a page-scoped
``chunk_id`` of the form ``{source}#p{page}:{position}`` so re-indexing upserts
in place even as pages shift. The OCR engine is expensive to build, so it is
constructed once per indexing run in :meth:`PdfChunker.begin_indexing` and
released in :meth:`PdfChunker.end_indexing`.

PDF support ships in the base install (``pypdf``, ``pypdfium2``,
``rapidocr-onnxruntime``), but the imports stay lazy so a core import never
drags the OCR stack in until a PDF is actually indexed.
"""

from collections.abc import Iterator
from pathlib import Path
from typing import Any

from duckdb_vectors.chunkers.filesystem import FilesystemChunker
from duckdb_vectors.chunkers.windowing import split_windows
from duckdb_vectors.models import Chunk

DEFAULT_TARGET_WORDS = 300
DEFAULT_OVERLAP_WORDS = 40
DEFAULT_MIN_CHARS = 16
DEFAULT_OCR_SCALE = 2.0

_IMPORT_ERROR = (
    "The 'pdf' chunker requires pypdf, pypdfium2, and rapidocr-onnxruntime. "
    "They ship with the base install; reinstall with 'pip install duckdb-vectors'."
)


class PdfChunker(FilesystemChunker):
    """Split PDF files into page-scoped chunks, OCR-ing scanned pages.

    ``target_words`` is the soft upper bound per chunk; longer pages are
    windowed with ``overlap_words`` of overlap. ``min_chars`` is the embedded
    text length at or below which a page is treated as scanned and routed
    through OCR. ``ocr_scale`` is the render scale for rasterising a scanned
    page before OCR.

    As a :class:`~duckdb_vectors.chunkers.filesystem.FilesystemChunker` it
    claims ``*.pdf`` files during a folder walk and reads each one in
    ``chunk_file``; the OCR engine is built once per run in ``begin_indexing``.
    """

    def __init__(
        self,
        target_words: int = DEFAULT_TARGET_WORDS,
        overlap_words: int = DEFAULT_OVERLAP_WORDS,
        min_chars: int = DEFAULT_MIN_CHARS,
        ocr_scale: float = DEFAULT_OCR_SCALE,
    ) -> None:
        self._target_words = target_words
        self._overlap_words = overlap_words
        self._min_chars = min_chars
        self._ocr_scale = ocr_scale
        self._engine: Any = None

    def handles(self, path: Path) -> bool:
        """Claim files with a ``.pdf`` suffix, case-insensitively."""
        return path.suffix.lower() == ".pdf"

    def begin_indexing(self) -> None:
        """Build the local OCR engine once for the whole run.

        Imports pypdf, pypdfium2, and RapidOCR lazily so a core import never
        pulls in the OCR stack. A missing dependency raises a clear
        ``ImportError`` naming the PDF requirements.
        """
        if self._engine is not None:
            return
        try:
            import pypdf  # noqa: F401
            import pypdfium2  # noqa: F401
            from rapidocr_onnxruntime import RapidOCR
        except ImportError as exc:  # pragma: no cover - import guard
            raise ImportError(_IMPORT_ERROR) from exc
        self._engine = RapidOCR()

    def end_indexing(self) -> None:
        """Release the OCR engine at the end of the run."""
        self._engine = None

    def _chunk_id(self, source: str, chunk: Chunk) -> str:
        """Page-scope the id as ``{source}#p{page}:{local_pos}``.

        ``local_pos`` is the per-page window index carried in ``metadata`` so
        the id stays page-local even though ``position`` runs across the whole
        document.
        """
        return f"{source}#p{chunk.metadata['page']}:{chunk.metadata['local_pos']}"

    def _ocr_page(self, path: Path, index: int) -> str:
        """Rasterise one page and read it with the local OCR engine.

        ``index`` is 0-based. Returns the recognised text joined by newlines,
        or an empty string when OCR surfaces nothing.
        """
        if self._engine is None:  # pragma: no cover - guarded by begin_indexing
            self.begin_indexing()
        import pypdfium2 as pdfium

        document = pdfium.PdfDocument(str(path))
        try:
            bitmap = document[index].render(scale=self._ocr_scale)
            image = bitmap.to_numpy()
        finally:
            document.close()
        result, _ = self._engine(image)
        if not result:
            return ""
        return "\n".join(line[1] for line in result)

    def chunk_file(self, path: Path) -> Iterator[Chunk]:
        """Read a PDF and yield one chunk per page window, in page order.

        For each page the embedded text is extracted first; a page whose text
        is shorter than ``min_chars`` is treated as scanned and read through
        OCR. ``position`` runs across the whole document while ``chunk_id`` is
        page-scoped. The placeholder ``source`` and ``chunk_id`` set here are
        re-derived by :meth:`FilesystemChunker.chunk_folder`.
        """
        import pypdf

        source = str(path)
        reader = pypdf.PdfReader(str(path))
        position = 0
        for index, reader_page in enumerate(reader.pages):
            page = index + 1
            text = reader_page.extract_text() or ""
            if len(text.strip()) < self._min_chars:
                text = self._ocr_page(path, index)
            for local_pos, window in enumerate(
                split_windows(text, self._target_words, self._overlap_words)
            ):
                if not window.strip():
                    continue
                yield Chunk(
                    chunk_id=f"{source}#p{page}:{local_pos}",
                    text=window,
                    source=source,
                    position=position,
                    title=f"Page {page}",
                    metadata={"page": page, "local_pos": local_pos},
                )
                position += 1
