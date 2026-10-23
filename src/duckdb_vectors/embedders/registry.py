"""Resolve a backend string to an :class:`Embedder` instance.

A backend string is either a bare name (``"fastembed"``, ``"hashing"``) or a
``name:variant`` pair (``"fastembed:BAAI/bge-small-en-v1.5"``,
``"hf:Qwen/Qwen3-Embedding-0.6B"``, ``"bedrock:titan"``). Factories are
registered by name; the variant (the part after the first colon) is passed
through to the factory.

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
    """Resolve a backend spec such as ``"fastembed"`` or ``"bedrock:titan"``.

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


def _fastembed_factory(variant: str | None) -> Embedder:
    from duckdb_vectors.embedders.fastembed import FastembedEmbedder

    return FastembedEmbedder(model_name=variant) if variant else FastembedEmbedder()


def _hf_factory(variant: str | None) -> Embedder:
    from duckdb_vectors.embedders.huggingface import HuggingFaceEmbedder

    if not variant:
        raise ValueError(
            "The 'hf' backend requires a repository id, e.g. "
            "'hf:Qwen/Qwen3-Embedding-0.6B'."
        )
    return HuggingFaceEmbedder(repo_id=variant)


def _bedrock_factory(variant: str | None) -> Embedder:
    from duckdb_vectors.embedders.bedrock import BedrockEmbedder

    return BedrockEmbedder(model=variant or "titan")


register_embedder("hashing", _hashing_factory)
register_embedder("fastembed", _fastembed_factory)
register_embedder("hf", _hf_factory)
register_embedder("bedrock", _bedrock_factory)
