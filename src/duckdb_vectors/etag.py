"""Content fingerprint used to reuse or recompute an embedding."""

from hashlib import sha256


def compute_etag(text: str, model_identifier: str, dimension: int) -> str:
    """Return the fingerprint that decides whether an embedding is reused.

    The etag covers everything that determines the stored vector: the exact
    string handed to the embedder plus the model identifier and dimension. A
    model or dimension change flips every etag so all vectors regenerate; an
    unchanged etag means the vector is identical and re-embedding is skipped.

    The chunk text is the payload; the model identifier and dimension are
    embedding-correctness guards, preventing silent reuse of a vector produced
    by a different backend.
    """
    payload = f"{model_identifier}\x00{dimension}\x00{text}"
    return sha256(payload.encode("utf-8")).hexdigest()
