"""Public datasets written out as Markdown knowledge bases, with validation sets.

The curation scripts (``scripts/kb_*.py``) work on a folder of Markdown pages,
as a knowledge manager's corpus is. These functions turn a public dataset into
such a folder and its questions into a ``validation.json`` whose golden chunks
are the folder's own chunk ids, so the whole path, from offline contexts in
front matter to indexing, is the one a user follows.

    $ .venv/bin/python -m scripts.benchmark_kb_datasets anthropic \\
        --folder tmp/kb/anthropic/pages --work tmp/kb/anthropic
"""

import argparse
import re
from collections import defaultdict
from pathlib import Path, PurePosixPath

import yaml

from development.curation import chunk_folder, write_json
from development.datasets import load_anthropic
from development.paths import BENCHMARK_CACHE
from scripts.kb_validation import Item, ValidationSet, freeze_split

_CACHE = BENCHMARK_CACHE
_BACKTICKS = re.compile(r"`+")
_LANGUAGES = {
    "py": "python",
    "rs": "rust",
    "go": "go",
    "ts": "typescript",
    "js": "javascript",
    "java": "java",
    "c": "c",
    "h": "c",
    "cpp": "cpp",
    "rb": "ruby",
}


def fence(code: str, language: str = "") -> str:
    """``code`` in a fenced block longer than any backtick run it contains."""
    longest = max((len(run) for run in _BACKTICKS.findall(code)), default=0)
    marks = "`" * max(4, longest + 1)
    return f"{marks}{language}\n{code.rstrip()}\n{marks}"


def page_for(path: str, front: dict, parts: list[tuple[int, str]]) -> str:
    """One Markdown page: front matter, the file name, one section per part."""
    name = PurePosixPath(path).name
    language = _LANGUAGES.get(PurePosixPath(path).suffix.lstrip("."), "")
    sections = [
        f"## {name} part {index}\n\n{fence(code, language)}" for index, code in parts
    ]
    header = yaml.safe_dump(front, sort_keys=False, allow_unicode=True).strip()
    return f"---\n{header}\n---\n# {name}\n\n" + "\n\n".join(sections) + "\n"


def prepare_anthropic(folder: Path, work: Path, cache: Path = _CACHE) -> dict:
    """Write the cookbook codebases as pages, one section per published chunk.

    Golden chunk ``(document, i)`` maps to every chunk of section ``Part i`` of
    its page: one chunk, unless the chunker windows a long section, in which
    case any of the windows satisfies it (an alternative). Native tags (repo,
    directory, extension) go in the front matter.
    """
    dataset = load_anthropic(cache)
    by_source: dict[str, list] = defaultdict(list)
    for chunk in dataset.chunks:
        by_source[chunk.source].append(chunk)
    folder.mkdir(parents=True, exist_ok=True)
    for source, chunks in by_source.items():
        meta = chunks[0].metadata
        front = {
            "repo": meta["repo_name"],
            "directory": meta["directory"],
            "extension": meta["extension"],
            "path": source,
        }
        parts = [(c.position, c.text) for c in sorted(chunks, key=lambda c: c.position)]
        target = folder / f"{source.lstrip('/')}.md"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(page_for(source, front, parts), encoding="utf-8")
    published = {c.chunk_id: (c.source.lstrip("/"), c.position) for c in dataset.chunks}
    windows: dict[tuple[str, int], list[str]] = defaultdict(list)
    for chunk in chunk_folder(folder):
        source = chunk.source.removesuffix(".md")
        match = re.search(r"part-(\d+)$", chunk.metadata.get("section_path", ""))
        if match:
            windows[(source, int(match.group(1)))].append(chunk.chunk_id)
    missing = [key for key in published.values() if key not in windows]
    if missing:
        raise ValueError(f"published chunks without a section: {missing[:3]}")
    validation = ValidationSet(corpus="anthropic-codebases")
    for number, query in enumerate(dataset.queries):
        groups = [windows[published[chunk_id]] for chunk_id in query.golden]
        validation.items.append(
            Item(
                id=f"native-{number:04d}",
                text=query.text,
                stratum="native",
                source=f"{published[query.golden[0]][0]}.md",
                golden=[cid for group in groups for cid in group],
                alternatives=groups,
                grades={cid: 1 for group in groups for cid in group},
            )
        )
    validation.log(
        "prepare",
        dataset="anthropic-codebases",
        sources=dataset.sources,
        pages=len(by_source),
        published_chunks=len(published),
        windowed_sections=sum(1 for ids in windows.values() if len(ids) > 1),
    )
    freeze_split(validation)
    write_json(work / "validation.json", validation.model_dump())
    return validation.steps[-2]


# FinanceBench (Islam et al. 2023), open sample: 150 questions written by
# financial analysts over 84 SEC filings, each with the evidence page. CC BY-NC
# 4.0: used for local benchmarking, derived pages are not republished.
FINANCEBENCH_COMMIT = "cc39aeb4afdf33909ee1412188bf89035950c2eb"
_FINANCEBENCH = (
    f"https://raw.githubusercontent.com/patronus-ai/financebench/{FINANCEBENCH_COMMIT}/"
)
_SQUASH = re.compile(r"\s+")


def _download(url: str, target: Path) -> Path:
    import urllib.request

    if not target.exists():
        target.parent.mkdir(parents=True, exist_ok=True)
        with urllib.request.urlopen(url, timeout=120) as response:
            target.write_bytes(response.read())
    return target


def squash(text: str) -> str:
    return _SQUASH.sub(" ", text).strip().lower()


def pdf_pages(path: Path) -> list[str]:
    """The text of every page of a PDF, as the package's PDF chunker reads it."""
    from pypdf import PdfReader

    return [page.extract_text() or "" for page in PdfReader(str(path)).pages]


def overlap(a: str, b: str) -> float:
    """Share of the words of ``a`` that appear in ``b``."""
    words_a, words_b = squash(a).split(), set(squash(b).split())
    return sum(w in words_b for w in words_a) / len(words_a) if words_a else 0.0


def financebench_page(document: str, front: dict, pages: list[str]) -> str:
    """One Markdown page per filing, one section per PDF page, text verbatim."""
    header = yaml.safe_dump(front, sort_keys=False, allow_unicode=True).strip()
    sections = [
        f"## {document} page {number}\n\n{fence(text, 'text')}"
        for number, text in enumerate(pages)
        if text.strip()
    ]
    return f"---\n{header}\n---\n# {document}\n\n" + "\n\n".join(sections) + "\n"


def prepare_financebench(folder: Path, work: Path, cache: Path = _CACHE) -> dict:
    """Write the 84 filings as pages and map each evidence page to its chunks.

    The evidence page number is checked against the extracted text: the page
    whose words best cover the evidence text is used, within two pages of the
    number given, and the quality report counts the shifts and the misses.
    Native tags (company, document type, fiscal period) come from the
    document information shipped with the dataset.
    """
    import json

    base = cache / "financebench"
    rows = [
        json.loads(line)
        for line in _download(
            _FINANCEBENCH + "data/financebench_open_source.jsonl",
            base / "financebench_open_source.jsonl",
        )
        .read_text(encoding="utf-8")
        .splitlines()
        if line.strip()
    ]
    info = {
        row["doc_name"]: row
        for row in (
            json.loads(line)
            for line in _download(
                _FINANCEBENCH + "data/financebench_document_information.jsonl",
                base / "financebench_document_information.jsonl",
            )
            .read_text(encoding="utf-8")
            .splitlines()
            if line.strip()
        )
    }
    documents = sorted({row["doc_name"] for row in rows})
    texts: dict[str, list[str]] = {}
    folder.mkdir(parents=True, exist_ok=True)
    for document in documents:
        pdf = _download(
            _FINANCEBENCH + f"pdfs/{document}.pdf", base / f"{document}.pdf"
        )
        texts[document] = pdf_pages(pdf)
        meta = info.get(document, {})
        front = {
            "company": meta.get("company", ""),
            "doc_type": meta.get("doc_type", ""),
            "period": str(meta.get("doc_period", "")),
            "sector": meta.get("gics_sector", ""),
        }
        (folder / f"{document}.md").write_text(
            financebench_page(document, front, texts[document]), encoding="utf-8"
        )
    windows: dict[tuple[str, int], list[str]] = defaultdict(list)
    for chunk in chunk_folder(folder):
        match = re.search(r"page-(\d+)$", chunk.metadata.get("section_path", ""))
        if match:
            windows[(chunk.source.removesuffix(".md"), int(match.group(1)))].append(
                chunk.chunk_id
            )
    quality = {"evidence": 0, "exact_page": 0, "shifted_page": 0, "missed": 0}
    validation = ValidationSet(corpus="financebench")
    for row in rows:
        groups = []
        for evidence in row["evidence"]:
            quality["evidence"] += 1
            document = evidence["doc_name"]
            given = int(evidence["evidence_page_num"])
            pages = texts[document]
            candidates = [n for n in range(given - 2, given + 3) if 0 <= n < len(pages)]
            best = max(
                candidates,
                key=lambda n: (
                    overlap(evidence["evidence_text"], pages[n]),
                    n == given,
                ),
                default=None,
            )
            if best is None or overlap(evidence["evidence_text"], pages[best]) < 0.6:
                quality["missed"] += 1
                continue
            quality["exact_page" if best == given else "shifted_page"] += 1
            if windows.get((document, best)):
                groups.append(windows[(document, best)])
        if not groups:
            continue
        validation.items.append(
            Item(
                id=row["financebench_id"],
                text=row["question"],
                stratum="native",
                answer=str(row.get("answer") or ""),
                source=f"{row['doc_name']}.md",
                golden=[cid for group in groups for cid in group],
                alternatives=groups,
                grades={cid: 1 for group in groups for cid in group},
            )
        )
    quality["questions"] = len(rows)
    quality["questions_used"] = len(validation.items)
    validation.log(
        "prepare",
        dataset="financebench",
        commit=FINANCEBENCH_COMMIT,
        pages=len(documents),
        **quality,
    )
    freeze_split(validation)
    write_json(work / "validation.json", validation.model_dump())
    return validation.steps[-2]


# LegalBench-RAG (Pipitone & Houir Alami 2024): questions over contracts and
# privacy policies with human-annotated character spans. The corpus comes from
# CUAD, ContractNLI and PrivacyQA (CC BY 4.0) and MAUD; it is used for local
# benchmarking and derived pages are not republished. The mini variant here is
# a seeded sample of 194 questions per sub-dataset with the corpus cut to the
# documents they cite. MAUD is left out: its 106 cited merger agreements would
# make about 21,000 chunks, more than the rest together several times over.
LEGALBENCH_SUBSETS = ("privacy_qa", "contractnli", "cuad")
LEGALBENCH_PER_SUBSET = 194
SECTION_WORDS = 250
_WORDS = re.compile(r"\S+")


def word_windows(text: str, size: int = SECTION_WORDS) -> list[tuple[int, int]]:
    """Character ranges of consecutive, non-overlapping windows of ``size`` words."""
    spans = [match.span() for match in _WORDS.finditer(text)]
    return [
        (spans[i][0], spans[min(i + size, len(spans)) - 1][1])
        for i in range(0, len(spans), size)
    ]


def legal_page(stem: str, front: dict, text: str, ranges: list) -> str:
    header = yaml.safe_dump(front, sort_keys=False, allow_unicode=True).strip()
    sections = [
        f"## {stem} part {n}\n\n{fence(text[a:b], 'text')}"
        for n, (a, b) in enumerate(ranges)
    ]
    return f"---\n{header}\n---\n# {stem}\n\n" + "\n\n".join(sections) + "\n"


def prepare_legalbench(folder: Path, work: Path, cache: Path = _CACHE) -> dict:
    """Write the cited documents as pages, one section per 250-word window.

    A snippet (a character span) is satisfied by any section that overlaps it,
    so each snippet is one evidence item, as in the cookbook's Pass@k. The data
    must have been unpacked into ``tmp/benchmarks/cache/legalbenchrag/data`` first.
    """
    import json

    data = cache / "legalbenchrag" / "data"
    if not (data / "benchmarks").exists():
        raise ValueError(f"unpack the LegalBench-RAG archive into {data}")
    rng_seed = 0
    validation = ValidationSet(corpus="legalbench-rag-mini")
    cited: dict[str, str] = {}
    sampled: list[tuple[str, dict]] = []
    import random

    for subset in LEGALBENCH_SUBSETS:
        tests = json.loads((data / "benchmarks" / f"{subset}.json").read_text())[
            "tests"
        ]
        picked = random.Random(rng_seed).sample(
            tests, min(LEGALBENCH_PER_SUBSET, len(tests))
        )
        for test in picked:
            sampled.append((subset, test))
            for snippet in test["snippets"]:
                cited[snippet["file_path"]] = subset
    ranges: dict[str, list[tuple[int, int]]] = {}
    folder.mkdir(parents=True, exist_ok=True)
    for path, subset in sorted(cited.items()):
        text = (data / "corpus" / path).read_text(encoding="utf-8")
        stem = Path(path).stem
        ranges[path] = word_windows(text)
        target = folder / f"{path}.md"
        target.parent.mkdir(parents=True, exist_ok=True)
        front = {"collection": subset, "document": stem}
        target.write_text(legal_page(stem, front, text, ranges[path]), "utf-8")
    sections: dict[tuple[str, int], list[str]] = defaultdict(list)
    for chunk in chunk_folder(folder):
        match = re.search(r"part-(\d+)$", chunk.metadata.get("section_path", ""))
        if match:
            key = (chunk.source.removesuffix(".md"), int(match.group(1)))
            sections[key].append(chunk.chunk_id)
    quality = {"snippets": 0, "snippets_mapped": 0}
    for number, (subset, test) in enumerate(sampled):
        groups = []
        for snippet in test["snippets"]:
            quality["snippets"] += 1
            start, end = snippet["span"]
            path = snippet["file_path"]
            group = [
                cid
                for n, (a, b) in enumerate(ranges[path])
                if a < end and start < b
                for cid in sections[(path, n)]
            ]
            if group:
                quality["snippets_mapped"] += 1
                groups.append(group)
        if not groups:
            continue
        validation.items.append(
            Item(
                id=f"{subset}-{number:04d}",
                text=test["query"],
                stratum="native",
                answer=" ".join(s.get("answer", "") for s in test["snippets"])[:2000],
                source=f"{test['snippets'][0]['file_path']}.md",
                golden=[cid for group in groups for cid in group],
                alternatives=groups,
                grades={cid: 1 for group in groups for cid in group},
            )
        )
    validation.log(
        "prepare",
        dataset="legalbench-rag-mini",
        subsets=list(LEGALBENCH_SUBSETS),
        per_subset=LEGALBENCH_PER_SUBSET,
        documents=len(cited),
        questions=len(sampled),
        questions_used=len(validation.items),
        **quality,
    )
    freeze_split(validation)
    write_json(work / "validation.json", validation.model_dump())
    return validation.steps[-2]


PREPARERS = {
    "anthropic": prepare_anthropic,
    "financebench": prepare_financebench,
    "legalbench": prepare_legalbench,
}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("dataset", choices=sorted(PREPARERS))
    parser.add_argument("--folder", type=Path, required=True)
    parser.add_argument("--work", type=Path, required=True)
    args = parser.parse_args(argv)
    print(PREPARERS[args.dataset](args.folder, args.work))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
