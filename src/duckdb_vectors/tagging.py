"""Tagged retrieval: project tags out of chunk metadata and build embedded text.

A tag is a ``label: $.path`` pair. The path is a JSONPath expression evaluated
over a chunk's metadata (the frontmatter of its document, plus the keys the
chunker adds) by DuckDB's own JSON engine, so there is no extra dependency and
users meet one dialect. DuckDB supports the common subset (member, index,
wildcard, recursive descent), not RFC 9535 filter expressions.

The projection is deterministic and needs no language model. Only the text that
is embedded changes: :func:`tagged_text` puts the tags in front of the chunk
text, while the stored ``Chunk.text`` stays raw. There is deliberately no cap on
the prefix length, because truncating would silently drop tags the knowledge
manager chose.
"""

import json
from collections.abc import Iterable

import duckdb
from pydantic import BaseModel

SEPARATOR = ";"
_LIST_JOIN = ", "


class TagField(BaseModel):
    """One tag to project: a ``label`` and the JSONPath ``path`` that fills it."""

    label: str
    path: str


def parse_tag_field(spec: str) -> TagField:
    """Parse ``"label: $.path"`` into a :class:`TagField`.

    The label is everything before the first colon, so a path may itself
    contain colons. Raises ``ValueError`` when the label is empty or the path
    does not start with ``$``.
    """
    label, colon, path = spec.partition(":")
    label, path = label.strip(), path.strip()
    if not colon or not label or not path.startswith("$"):
        raise ValueError(
            f"Invalid tag field {spec!r}: expected 'label: $.path', "
            "for example 'product: $.product.name'."
        )
    return TagField(label=label, path=path)


def parse_tag_fields(specs: str | Iterable[str] | None) -> list[TagField]:
    """Parse an ordered list of tag specs, or one ``;``-separated string.

    The string form is what ``DUCKDB_VECTORS_TAG_FIELDS`` carries. Blank items
    are ignored, and ``None`` means no tags.
    """
    if specs is None:
        return []
    items = specs.split(SEPARATOR) if isinstance(specs, str) else list(specs)
    return [parse_tag_field(item) for item in items if item.strip()]


def _join(value: object) -> str:
    """Render one extraction result: lists are comma-joined, nulls dropped."""
    if value is None:
        return ""
    if isinstance(value, list):
        return _LIST_JOIN.join(str(item) for item in value if item is not None)
    return str(value)


def project_many(
    metadatas: list[dict], fields: list[TagField]
) -> list[list[tuple[str, str]]]:
    """Project the tags of every metadata dict, in input order.

    Returns, for each metadata dict, the ``(label, value)`` pairs in the order
    of ``fields``. A tag whose path finds nothing, or only empty values, is
    left out. Identical metadata is evaluated once, and each field runs as one
    DuckDB statement over all distinct metadata.
    """
    if not fields or not metadatas:
        return [[] for _ in metadatas]
    documents = [json.dumps(metadata, sort_keys=True) for metadata in metadatas]
    unique = list(dict.fromkeys(documents))
    con = duckdb.connect()
    try:
        columns: list[list[str]] = []
        for field in fields:
            rows = con.execute(
                "SELECT json_extract_string(d::JSON, ?) "
                "FROM (SELECT unnest(?) AS d, generate_subscripts(?, 1) AS i) "
                "ORDER BY i",
                [field.path, unique, unique],
            ).fetchall()
            columns.append([_join(row[0]) for row in rows])
    finally:
        con.close()
    index = {document: position for position, document in enumerate(unique)}
    result: list[list[tuple[str, str]]] = []
    for document in documents:
        position = index[document]
        result.append(
            [
                (field.label, column[position])
                for field, column in zip(fields, columns, strict=True)
                if column[position]
            ]
        )
    return result


def project_tags(metadata: dict, fields: list[TagField]) -> list[tuple[str, str]]:
    """Project the tags of one metadata dict; see :func:`project_many`."""
    return project_many([metadata], fields)[0]


def tag_prefix(tags: list[tuple[str, str]]) -> str:
    """Render tags as one ``label: value`` line each; empty for no tags."""
    return "\n".join(f"{label}: {value}" for label, value in tags)


def tagged_text(text: str, tags: list[tuple[str, str]]) -> str:
    """Return the text to embed: a ``label: value`` line per tag, a blank line,
    then the chunk text. With no tags the text is returned unchanged, so a
    corpus without tagging embeds exactly what it always did.
    """
    if not tags:
        return text
    return f"{tag_prefix(tags)}\n\n{text}"
