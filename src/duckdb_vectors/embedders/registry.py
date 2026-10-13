"""Resolve a backend string to an :class:`Embedder` instance.

A backend string is either a bare name (``"local"``, ``"hashing"``) or a
``name:variant`` pair (``"bedrock:titan"``). Factories are registered by name;
the variant (the part after the first colon) is passed through to the factory.

Contributors add open or closed, local or cloud backends by calling
:func:`register_embedder` with a factory, keeping the core free of heavy
dependencies.
"""

from collections.abc import Callable

from duckdb_vectors.embedders.base import Embedder
from duckdb_vectors.embedders.hashing import HashingEmbedder

EmbedderFactory = Callable[[str | None], Embedder]

_REGISTRY: dict[str, EmbedderFactory] = {}


def register_embedder(name: str, factory: EmbedderFactory) -> None:
    """Register a backend factory under ``name``.

    The factory receives the variant string (the part after the first colon in
    the backend spec) or ``None`` when no variant is given.
    """
    _REGISTRY[name] = factory


def resolve_embedder(spec: str) -> Embedder:
    """Resolve a backend spec such as ``"local"`` or ``"bedrock:titan"``.

    Raises ``ValueError`` for an unknown backend name, listing the registered
    names to aid discovery.
    """
    name, _, variant = spec.partition(":")
    factory = _REGISTRY.get(name)
    if factory is None:
        known = ", ".join(sorted(_REGISTRY)) or "(none)"
        raise ValueError(
            f"Unknown embedding backend '{name}'. Registered backends: {known}."
        )
    return factory(variant or None)


def _hashing_factory(variant: str | None) -> Embedder:
    if variant:
        return HashingEmbedder(dimensions=int(variant))
    return HashingEmbedder()


def _local_factory(variant: str | None) -> Embedder:
    from duckdb_vectors.embedders.local import LocalEmbedder

    return LocalEmbedder(model_name=variant) if variant else LocalEmbedder()


def _bedrock_factory(variant: str | None) -> Embedder:
    from duckdb_vectors.embedders.bedrock import BedrockEmbedder

    return BedrockEmbedder(model=variant or "titan")


register_embedder("hashing", _hashing_factory)
register_embedder("local", _local_factory)
register_embedder("bedrock", _bedrock_factory)
