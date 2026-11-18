"""Write an offline context for every section of a Markdown knowledge base.

This is Anthropic's Contextual Retrieval moved upstream: a language model reads
each page once and writes, for each of its sections, the short context that
situates the section within the page. The prompt is Anthropic's, word for word.
The contexts are stored in the page's own front matter, under the reserved
``sections`` mapping keyed by ``section_path``::

    sections:
      setup/retries:
        llm:
          context: The retry settings of the Cedar deployment runbook ...

The Markdown chunker merges each entry into that section's chunks, so the
context is indexed like any other tag, with ``--tag-fields "context:
$.llm.context"`` (and ``--tag-bm25`` for the keyword index). No model runs at
chunking or indexing time. The page is sent once as a cached prompt prefix and
then read from the cache for its other sections. Answers are cached on disk by
content, so a re-run costs nothing and an edited page costs one page.

    $ make kb-context KB=path/to/pages WORK=tmp/kb/work
    $ .venv/bin/python -m scripts.kb_context FOLDER --work DIR [--dry-run]

The script rewrites the ``sections`` key of the pages it is given and nothing
else; it never commits. Review the change before committing it.
"""

import argparse
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from duckdb_vectors.chunkers.markdown import MarkdownChunker
from scripts.kb_common import (
    DEFAULT_MODEL,
    DEFAULT_REGION,
    AnswerCache,
    Model,
    load_prompt,
    pages,
    read_front_matter,
    set_front_matter_key,
    split_front_matter,
    write_json,
)

PROMPT = "contextual-header"
NAMESPACE = "llm"
FIELD = "context"
_SPLIT = "Here is the chunk we want to situate"


def blocks_for(document: str, section: str) -> list[dict]:
    """The user message: the page (a cacheable prefix), then the section."""
    text = load_prompt(PROMPT).render(document=document, chunk=section)
    head, marker, tail = text.partition(_SPLIT)
    return [
        {"text": head.rstrip()},
        {"cachePoint": {"type": "default"}},
        {"text": marker + tail},
    ]


def page_sections(chunker: MarkdownChunker, text: str) -> dict[str, str]:
    """``{section_path: body}``; repeated paths are joined, in page order."""
    bodies: dict[str, list[str]] = {}
    for path, _, body in chunker.sections(text):
        bodies.setdefault(path, []).append(body)
    return {path: "\n\n".join(parts) for path, parts in bodies.items()}


def window(document: str, ordered: list[str], index: int, limit: int) -> str:
    """A stand-in for a page longer than ``limit`` characters.

    The opening of the page (half the budget), then the section and its
    neighbours (the other half), so the model still sees what the page is
    about and where the section sits. Without caching across sections, this
    bounds the cost of very long pages, at the price of a partial view.
    """
    half = limit // 2
    near = "\n\n".join(ordered[max(0, index - 1) : index + 2])
    if len(near) > half:
        start = near.find(ordered[index][:200])
        near = near[max(0, start - half // 4) :][:half]
    return f"{document[:half]}\n\n[...]\n\n{near}"


def contexts_for_page(
    text: str,
    chunker: MarkdownChunker,
    model: Model,
    cache: AnswerCache,
    max_document_chars: int | None = None,
) -> dict[str, str]:
    """The context of every section of one page, cached by content.

    With ``max_document_chars``, a longer page is replaced, for each section,
    by its opening and the section's neighbourhood (see :func:`window`).
    """
    prompt = load_prompt(PROMPT)
    _, body = split_front_matter(text)
    full = body.strip()
    sections = page_sections(chunker, text)
    ordered = list(sections.values())
    result: dict[str, str] = {}
    for index, (path, section) in enumerate(sections.items()):
        document = full
        if max_document_chars and len(full) > max_document_chars:
            document = window(full, ordered, index, max_document_chars)
        key = cache.key(prompt.name, prompt.version, model.model_id, document, section)
        answer = cache.get(key)
        if answer is None:
            answer = cache.put(
                key, model(prompt.system, blocks_for(document, section), 300)
            )
        result[path] = " ".join(str(answer).split())
    return result


def merge_sections(front: dict, contexts: dict[str, str]) -> dict:
    """The page's ``sections`` mapping with ``llm.context`` set per section.

    Other keys of existing entries are kept; contexts of sections that no
    longer exist are dropped.
    """
    existing = front.get("sections")
    merged: dict = {}
    if isinstance(existing, dict):
        for path, entry in existing.items():
            if not isinstance(entry, dict):
                continue
            entry = dict(entry)
            llm = dict(entry.get(NAMESPACE) or {})
            llm.pop(FIELD, None)
            if llm:
                entry[NAMESPACE] = llm
            else:
                entry.pop(NAMESPACE, None)
            if entry:
                merged[str(path)] = entry
    for path, context in contexts.items():
        entry = dict(merged.get(path, {}))
        entry[NAMESPACE] = {**dict(entry.get(NAMESPACE) or {}), FIELD: context}
        merged[path] = entry
    return merged


def contextualize_folder(
    folder: Path,
    model: Model,
    cache: AnswerCache,
    workers: int = 8,
    max_document_chars: int | None = None,
) -> dict[str, dict[str, str]]:
    """``{page: {section_path: context}}`` for every page of the folder."""
    files = pages(folder)

    def one(path: Path) -> dict[str, str]:
        chunker = MarkdownChunker()
        try:
            return contexts_for_page(
                path.read_text(encoding="utf-8"),
                chunker,
                model,
                cache,
                max_document_chars,
            )
        finally:
            chunker.end_indexing()

    with ThreadPoolExecutor(workers) as pool:
        results = list(pool.map(one, files))
    return {
        str(path.relative_to(folder)): contexts
        for path, contexts in zip(files, results, strict=True)
    }


def apply_contexts(folder: Path, contexts: dict[str, dict[str, str]]) -> int:
    """Write the contexts into the pages' front matter; returns pages changed."""
    changed = 0
    for name, page_contexts in contexts.items():
        path = folder / name
        text = path.read_text(encoding="utf-8")
        merged = merge_sections(read_front_matter(text), page_contexts)
        updated = set_front_matter_key(text, "sections", merged or None)
        if updated != text:
            path.write_text(updated, encoding="utf-8")
            changed += 1
    return changed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("folder", type=Path)
    parser.add_argument("--work", type=Path, default=Path("tmp/kb"))
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--region", default=DEFAULT_REGION)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument(
        "--max-document-chars",
        type=int,
        default=None,
        help="for longer pages, send the opening and the section's neighbours "
        "instead of the whole page (bounds the cost of very long pages)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="write the contexts to WORK/contexts.json only, not into the pages",
    )
    args = parser.parse_args(argv)
    model = Model(args.model, args.region)
    cache = AnswerCache(args.work / "cache" / "context", model.usage)
    contexts = contextualize_folder(
        args.folder, model, cache, args.workers, args.max_document_chars
    )
    sizes = sorted(
        len(text.split()) for page in contexts.values() for text in page.values()
    )
    report = {
        "prompt": f"{PROMPT}.v{load_prompt(PROMPT).version}",
        "model": args.model,
        "pages": len(contexts),
        "sections": len(sizes),
        "median_words": sizes[len(sizes) // 2] if sizes else 0,
        "max_document_chars": args.max_document_chars,
        "usage": model.usage.report(),
    }
    write_json(args.work / "contexts.json", {**report, "contexts": contexts})
    if not args.dry_run:
        report["pages_changed"] = apply_contexts(args.folder, contexts)
    write_json(args.work / "context-report.json", report)
    print(report, file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
