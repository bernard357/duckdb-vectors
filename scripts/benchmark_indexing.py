"""Measure indexing time for duckdb-vectors: first index, re-index, edits.

Indexes a folder of Markdown with ``index_folder`` and times the operations the
performance workbook reports:

- a first full index into an empty database (chunk, embed, write, build the
  full-text index);
- a re-index of the unchanged folder (nothing re-embedded or written), and how
  long the database file stays locked during it;
- a re-index after editing one file (only its chunks re-embedded and written).

By default the folder is synthetic: ``--files`` Markdown pages generated from a
fixed seed, each with a few headed sections, so anyone can reproduce the run.
Pass ``--folder`` to measure a real corpus instead; the report then describes it
by shape only (files and chunks), never by name or content.

Run it from the repository root with the project venv:

    $ .venv/bin/python scripts/benchmark_indexing.py
    $ .venv/bin/python scripts/benchmark_indexing.py --files 1000
    $ .venv/bin/python scripts/benchmark_indexing.py --folder /path/to/docs
    $ .venv/bin/python scripts/benchmark_indexing.py --backend hashing --json out.json

The embedding model is loaded before timing starts, so the figures exclude its
one-time download and load. Nothing here is wired into the test suite.
"""

import argparse
import json
import random
import shutil
import sys
import tempfile
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "src"))

from duckdb_vectors import VectorStore, index_folder  # noqa: E402

DEFAULT_BACKEND = "fastembed:minishlab/potion-multilingual-128M"
DEFAULT_FILES = 250
SEED = 20261103


def _vocabulary(rng: random.Random, size: int = 5_000) -> list[str]:
    """Letter-only words: the full-text tokenizer drops digits."""
    letters = "abcdefghijklmnopqrstuvwxyz"
    words: set[str] = set()
    while len(words) < size:
        words.add("".join(rng.choice(letters) for _ in range(rng.randint(3, 9))))
    return sorted(words)


def _write_synthetic(folder: Path, files: int) -> None:
    """Write ``files`` Markdown pages with headed sections, from a fixed seed.

    Sizes vary like a real documentation folder: 2 to 8 sections per page and
    60 to 400 words per section, so the chunker produces a mix of single and
    windowed sections.
    """
    rng = random.Random(SEED)
    vocabulary = _vocabulary(rng)
    for number in range(files):
        lines = [f"# Page {number}", ""]
        for section in range(rng.randint(2, 8)):
            words = rng.choices(vocabulary, k=rng.randint(60, 400))
            lines += [f"## Section {section}", "", " ".join(words), ""]
        (folder / f"page-{number:04d}.md").write_text("\n".join(lines))


def _timed_index(store: VectorStore, folder: Path) -> tuple[float, object, float]:
    """Run ``index_folder``; return its duration, report, and time locked.

    The lock window is the time the store held its database connection: the
    indexer connects only for its lookups and writes, so this is how long other
    processes sharing the file would wait.
    """
    held = [0.0]
    opened = [0.0]
    connect = store._connect
    close = VectorStore.close

    def timed_connect():
        connection = connect()
        opened[0] = time.perf_counter()
        return connection

    def timed_close(self):
        if self is store and self._connection is not None:
            held[0] += time.perf_counter() - opened[0]
        close(self)

    store._connect = timed_connect  # type: ignore[method-assign]
    VectorStore.close = timed_close  # type: ignore[method-assign]
    try:
        start = time.perf_counter()
        report = index_folder(store, folder, recursive=True)
        store.close()
        return time.perf_counter() - start, report, held[0]
    finally:
        VectorStore.close = close  # type: ignore[method-assign]
        del store._connect


def _edit_one_file(folder: Path) -> None:
    """Append a sentence to the first Markdown file, changing its last chunk."""
    first = sorted(folder.rglob("*.md"))[0]
    first.write_text(first.read_text() + "\nAn added sentence for the benchmark.\n")


def benchmark(folder: Path, backend: str) -> dict:
    with tempfile.TemporaryDirectory(prefix="ddbv_index_") as directory:
        work = Path(directory) / "docs"
        shutil.copytree(folder, work)
        db_path = str(Path(directory) / "index.duckdb")
        store = VectorStore(db_path=db_path, backend=backend)
        store.embedder.embed(["warm-up"])  # exclude model download and load

        first_s, first, first_lock = _timed_index(store, work)
        noop_s, noop, noop_lock = _timed_index(store, work)
        _edit_one_file(work)
        edit_s, edit, edit_lock = _timed_index(store, work)
        size_mb = Path(db_path).stat().st_size / (1024 * 1024)

    return {
        "backend": backend,
        "files": first.files,
        "chunks": first.chunks,
        "db_size_mb": round(size_mb, 1),
        "first_index": {"seconds": first_s, "written": first.written},
        "noop_reindex": {
            "seconds": noop_s,
            "written": noop.written,
            "locked_seconds": noop_lock,
        },
        "one_file_edit": {
            "seconds": edit_s,
            "embedded": edit.embedded,
            "written": edit.written,
            "locked_seconds": edit_lock,
        },
        "first_index_locked_seconds": first_lock,
    }


def _markdown_table(result: dict) -> str:
    noop = result["noop_reindex"]
    edit = result["one_file_edit"]
    rows = [
        "| Operation | Time | Rows written | File locked |",
        "| --- | --- | --- | --- |",
        f"| First index | {result['first_index']['seconds']:.1f} s "
        f"| {result['first_index']['written']:,} "
        f"| {result['first_index_locked_seconds']:.2f} s |",
        f"| Re-index, unchanged | {noop['seconds']:.2f} s | {noop['written']} "
        f"| {noop['locked_seconds']:.3f} s |",
        f"| Re-index, one file edited | {edit['seconds']:.2f} s "
        f"| {edit['written']} | {edit['locked_seconds']:.3f} s |",
    ]
    return "\n".join(rows)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--folder",
        type=Path,
        help="index this folder of Markdown/PDF instead of a synthetic one",
    )
    parser.add_argument(
        "--files",
        type=int,
        default=DEFAULT_FILES,
        help=f"synthetic pages to generate (default: {DEFAULT_FILES})",
    )
    parser.add_argument(
        "--backend",
        default=DEFAULT_BACKEND,
        help="embedding backend (default: the static fastembed model)",
    )
    parser.add_argument("--json", metavar="PATH", help="write raw results as JSON")
    args = parser.parse_args(argv)

    with tempfile.TemporaryDirectory(prefix="ddbv_corpus_") as directory:
        folder = args.folder
        if folder is None:
            folder = Path(directory)
            _write_synthetic(folder, args.files)
            label = f"synthetic corpus, seed {SEED}"
        else:
            label = "real corpus"
        result = benchmark(folder, args.backend)

    print(
        f"\n{label}: {result['files']} files, {result['chunks']:,} chunks, "
        f"{result['db_size_mb']} MB, backend {args.backend}\n"
    )
    print(_markdown_table(result))
    if args.json:
        Path(args.json).write_text(json.dumps(result, indent=2), encoding="utf-8")
        print(f"\nwrote raw results to {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
