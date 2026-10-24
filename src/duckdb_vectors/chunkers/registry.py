"""Resolve a chunker string to a :class:`Chunker` instance.

A chunker string is either a bare name (``"markdown"``) or a ``name:variant``
pair. Factories are registered by name; the variant (the part after the first
colon) is passed through to the factory. The variant is accepted but unused for
now — the mechanism is kept so variant semantics can be added later without
changing the public shape.

Contributors add their own splitters by calling :func:`register_chunker` with a
factory, keeping the core free of heavy dependencies.
"""

from collections.abc import Callable

from duckdb_vectors.chunkers.base import Chunker

ChunkerFactory = Callable[[str | None], Chunker]

_REGISTRY: dict[str, ChunkerFactory] = {}


def register_chunker(name: str, factory: ChunkerFactory) -> None:
    """Register a chunker factory under ``name``.

    The factory receives the variant string (the part after the first colon in
    the chunker spec) or ``None`` when no variant is given.
    """
    _REGISTRY[name] = factory


def resolve_chunker(spec: str) -> Chunker:
    """Resolve a chunker spec such as ``"markdown"``.

    Raises ``ValueError`` for an unknown chunker name, listing the registered
    names to aid discovery.
    """
    name, _, variant = spec.partition(":")
    factory = _REGISTRY.get(name)
    if factory is None:
        known = ", ".join(sorted(_REGISTRY)) or "(none)"
        raise ValueError(f"Unknown chunker '{name}'. Registered chunkers: {known}.")
    return factory(variant or None)


def _markdown_factory(variant: str | None) -> Chunker:
    from duckdb_vectors.chunkers.markdown import MarkdownChunker

    return MarkdownChunker()


def _pdf_factory(variant: str | None) -> Chunker:
    from duckdb_vectors.chunkers.pdf import PdfChunker

    return PdfChunker()


register_chunker("markdown", _markdown_factory)
register_chunker("pdf", _pdf_factory)
