# Developer workbook

Questions and answers for contributors to the `duckdb-vectors` project: prerequisites,
setup, the Makefile workflow, testing, project principles, and how changes land.

You may also look at the
[User guide](duckdb-vectors-guide.md) and at the [Technical design](duckdb-vectors-primitive.md) for more information.

## What are good first contributions?

You do not need to understand the whole codebase to help:

- __Add an embedding backend__ — the main extension point; see
  [below](#how-do-i-add-an-embedding-backend).
- __Add a chunker__ — for a format not covered well yet; see
  [below](#how-do-i-add-a-chunker).
- __Improve the docs__ — clarify a page, add a worked example under
  `examples/`, or document a pattern.
- __Share a ranking recipe__ — a neat injectable `vector_sql` / `text_sql` for
  scoping or decay.
- __Report a rough edge__ — a clear issue with a minimal reproduction.

If unsure where to start, open an issue describing what you want to do.

## What principles should a change follow?

Contributions land fastest when they fit the project's shape:

- __Do one thing well.__ This is a retrieval building block — store, index,
  search. Generation, prompting, and LLM orchestration belong in the harness.
- __Boring, composable technology.__ Prefer the standard library and DuckDB's
  own features over new dependencies. Heavy dependencies belong behind an
  optional extra.
- __Framework-agnostic.__ No hard dependency on a specific agent framework.
- __Read-mostly and fast.__ The serving path opens read-only and answers in
  tens of milliseconds.

## How do I add an embedding backend?

The embedder registry is the main extension point. A backend is a small class
exposing `model_identifier`, `dimensions`, and `embed(texts)`, registered with
`register_embedder(name, factory)`. It can be open or closed, local or cloud —
the registry is designed for a growing set. See `src/duckdb_vectors/embedders/`
for the hashing, fastembed, and Bedrock examples, and keep heavy dependencies
behind an optional extra in `pyproject.toml`.

The core `hf:<repo-id>` backend (`embedders/huggingface.py`) loads the large
majority of ONNX embedding exports on the Hub across three graph shapes, all
handled additively so no working model regresses:

- __Simple encoders__ — `input_ids` / `attention_mask` (/ `token_type_ids`)
  feeding a `last_hidden_state` output (bge, e5, sentence-transformers).
- __External-data ONNX__ — larger exports whose weights sit in a sibling
  `model.onnx_data`. `snapshot_download` fetches the ONNX folder, and the
  loader copies the real files into one directory so the ONNX runtime finds the
  data file beside the graph; `hf_hub_download` of the single file would
  symlink the graph away from its data file ("External data path escapes model
  directory").
- __Decoder-style graphs__ — Qwen3-Embedding and relatives that declare
  `position_ids` and per-layer `past_key_values.*` cache inputs. The loader
  feeds `position_ids` synthesized from the attention mask (`cumsum − 1`,
  clamped at 0, int64), feeds zero-length cache tensors shaped
  `[batch, kv_heads, 0, head_dim]` with `kv_heads` / `head_dim` read as
  concrete ints from `session.get_inputs()` shapes (never hardcoded), and then
  selects the hidden-state output by name from `session.get_outputs()` —
  preferring `last_hidden_state`, else the first float output that is neither
  `logits` nor a `present.*` cache output — before pooling. The opt-in `model`
  test loads `onnx-community/Qwen3-Embedding-0.6B-ONNX` (external-data +
  decoder, 1024-dimensional `last_hidden_state`) to exercise all three.

## How do I add a chunker?

Subclass `FilesystemChunker` and declare a `mimetypes` frozenset of the MIME
types the chunker claims (for example `frozenset({"application/pdf"})`), then
implement `chunk_file(path)` — read one file and yield its chunks with stable
ids. The base `handles(path)` resolves a path's MIME type via the stdlib
`mimetypes.guess_type` and matches it against the declared set, so a chunker
claims files by MIME type rather than by suffix; `handles` stays overridable as
a hook for refining matching when MIME detection is ambiguous. The base provides
the recursive `chunk_folder` walk, sets each chunk's `source` to its
folder-relative path, and gives no-op defaults for the three lifecycle hook pairs
(`begin_indexing` / `end_indexing`, `begin_folder` / `end_folder`,
`begin_file` / `end_file`). The run-level pair is where to acquire and release an
expensive resource once — for example the planned PDF chunker will load a local
OCR engine in `begin_indexing` and release it in `end_indexing`, then emit one
chunk per page from `chunk_file`.

The text-level `chunk(text, source)` method stays available for feeding raw text
directly. See `src/duckdb_vectors/chunkers/` for the base and the markdown
chunker, and pass your chunker to `index_folder` or the store.

Once registered with `register_chunker`, a new chunker participates in the
`composite` chunker for free: `composite` dispatches matching files to it with
no extra wiring, so a mixed-format folder picks up your format automatically.

## What are the documentation conventions?

Documentation lives in `documentation/`, GitHub-Flavored Markdown with mermaid
(no ASCII art), ATX sentence-case headers, and fenced code blocks. Two naming
conventions keep the set expandable:

- __Platform pages carry a provider prefix__ — `aws-bedrock-agentcore-guide.md`. Other platforms follow the same shape with their own prefix (`gcp-`, `azure-`, `modal-`), typically a `-guide.md` (teach-by-example) and a -workbook.md` (FAQ) pair.
- __Use-case pages are step-by-step procedures__ named `setup-{capability}-procedure.md`. One page per use case keeps the set growing without inflating any single page.

## How do I open a good pull request?

- Keep each pull request focused on a single change.
- Write a clear description: what changed and why.
- Match the surrounding style; Ruff decides formatting.
- Update the README or docstrings when you change behavior.
- Run `make lint` and `make test` first; both should pass.
- Be kind in review.

## How do I report an issue?

Open an issue with what you expected and what happened, a minimal snippet or
steps to reproduce, and your Python version plus how the store was configured
(backend, read-only).

## What are the prerequisites?

Install these before running `make setup`. The first three are genuine
prerequisites `make setup` cannot install for you; it checks they are present
and installs the rest.

| Tool | Why | How to install |
| --- | --- | --- |
| [Python](https://www.python.org/) 3.12+ | Runs the package and the tests | python.org, pyenv, or your package manager |
| [uv](https://docs.astral.sh/uv/) | Creates the venv, installs deps, builds | `curl -LsSf https://astral.sh/uv/install.sh \| sh` or `brew install uv` |
| [Docker](https://docs.docker.com/get-docker/) | Builds and runs the example container agents | Docker Desktop, or your package manager |
| [Node.js](https://nodejs.org/) + npm | Provides markdownlint-cli for Markdown linting | nodejs.org, nvm, or your package manager |
| [hadolint](https://github.com/hadolint/hadolint) | Lints the example Dockerfiles | Per your platform — see the [hadolint install guide](https://github.com/hadolint/hadolint#install) (binary release, `hadolint/hadolint` Docker image, or a native package) |

Ruff and pytest are not prerequisites — `make setup` installs them into the
virtual environment via the `[dev]` extra.

## What does each Makefile target do?

The `Makefile` consolidates the most frequent commands used throughout this project. Run `make` with no target to list them. The essentials:

- `make setup` — check prerequisites, create the `.venv`, install tooling
- `make lint` — Ruff, mypy, Markdown, and Dockerfile checks
- `make test` — the pytest suite
- `make coverage` — the pytest suite under coverage, writing the HTML report
- `make serve-coverage` — serve the HTML coverage report in a browser
- `make format` — apply Ruff formatting
- `make push` — rebase onto `main`, then push
- `make build` — build the wheel and sdist with uv
- `make clean` — remove build artifacts and caches (including `*.out`)

## How do I set up a development environment?

```shell
$ git clone https://github.com/bernard357/duckdb-vectors.git
$ cd duckdb-vectors
$ make setup   # checks prerequisites, creates the venv, installs tooling
```

`make setup` runs `check-prerequisites`, then `setup-python` (uv venv + the package with all extras) and `setup-markdownlint` (npm). `setup-hadolint` checks whether hadolint is present and, if not, points you at the official install guide — hadolint has no pip or npm package, so install it for your platform (binary release, Docker image, or a native package). Run `make setup-python` for just the Python environment if you manage the linters yourself.

## What does the lint cover?

```shell
$ make lint        # ruff check + ruff format --check + mypy, markdownlint, hadolint
```

Python (Ruff lint and format, plus mypy type checking over `src/duckdb_vectors`
via `[tool.mypy]`), Markdown (markdownlint via `.markdownlint.json`), and
Dockerfiles (hadolint via `.hadolint.yaml`). mypy verifies the type annotations
the package ships behind its `py.typed` marker. Use `make format` to apply
Python formatting. All checks should pass before you open a pull request.

## How do I run the tests?

```shell
$ make test        # pytest -q
```

The suite uses the dependency-free `hashing` backend, so it needs no model
download and no network and runs in a few seconds. Add a test for any behavior
you change or add; the suite under `tests/` shows the patterns (temp-file
DuckDB fixtures, deterministic embeddings).

One test is held out of this default run by the `model` marker: an opt-in
end-to-end test that downloads the real `onnx-community/Qwen3-Embedding-0.6B-ONNX`
export through the `hf:` backend and embeds text (external-data + decoder graph,
1024-dimensional output). `make test` runs `-m "not model"` so it stays offline
and fast; the model test never fires there. Run it explicitly when you want to
exercise the real download, external-data co-location, ONNX session, tokenizer,
position_ids / empty-KV-cache plumbing, and last-token-pooling path:

```shell
$ .venv/bin/pytest -m model   # downloads the model once (HF-cached), then embeds
```

It downloads a multi-hundred-MB model on first run and is skipped (not failed)
when the model cannot be fetched, so a disconnected run degrades to a skip. It
is also picked up automatically by `make coverage`, which runs unfiltered.

## How do I measure test coverage?

```shell
$ make coverage        # pytest --cov, write htmlcov/, print the term summary
$ make serve-coverage  # serve htmlcov/ over HTTP and open it in the browser
```

`make coverage` runs the same suite under `pytest-cov` (in the `[dev]` extra),
scoped to `src/duckdb_vectors` with line and branch measurement. It prints a
`term-missing` summary, writes an HTML report to `htmlcov/`, and then exits —
suggesting `make serve-coverage` rather than launching a server itself, so the
target stays non-blocking for CI and automation that wrap it.

`make serve-coverage` serves `htmlcov/` over a local HTTP server and opens it in
your browser — click into a file to see which lines and branches the tests
never hit. It is served over HTTP rather than opened as a `file://` path on
purpose: coverage's per-file pages use `localStorage`, which browsers block on
a `file://` origin, so a file-opened report errors when you click into a file.
The server picks a free port on `127.0.0.1`, runs in the foreground, and prints
its URL; stop it with Ctrl-C.

The report is advisory: there is no `--cov-fail-under` gate, so `make coverage`
never fails on a low number. Both `htmlcov/` and the `.coverage` data file are
gitignored and removed by `make clean`.
