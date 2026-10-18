# Developer workbook

Questions and answers for contributors to the `duckdb-vectors` project: prerequisites,
setup, the Makefile workflow, testing, project principles, and how changes land.

You may also look at the
[User guide](duckdb-vectors-guide.md) and at the [Technical design](duckdb-vectors-primitive.md) for more information.

## What are good first contributions? {#first-contributions}

You do not need to understand the whole codebase to help:

- __Add an embedding backend__ — the main extension point; see
  [below](#backend).
- __Add a chunker__ — for a format not covered well yet; see
  [below](#chunker).
- __Improve the docs__ — clarify a page, add a worked example under
  `examples/`, or document a pattern.
- __Share a ranking recipe__ — a neat injectable `vector_sql` / `text_sql` for
  scoping or decay.
- __Report a rough edge__ — a clear issue with a minimal reproduction.

If unsure where to start, open an issue describing what you want to do.

## What principles should a change follow? {#principles}

Contributions land fastest when they fit the project's shape:

- __Do one thing well.__ This is a retrieval building block — store, index,
  search. Generation, prompting, and LLM orchestration belong in the harness.
- __Boring, composable technology.__ Prefer the standard library and DuckDB's
  own features over new dependencies. Heavy dependencies belong behind an
  optional extra.
- __Framework-agnostic.__ No hard dependency on a specific agent framework.
- __Read-mostly and fast.__ The serving path opens read-only and answers in
  tens of milliseconds.

## How do I add an embedding backend? {#backend}

The embedder registry is the main extension point. A backend is a small class
exposing `model_identifier`, `dimensions`, and `embed(texts)`, registered with
`register_embedder(name, factory)`. It can be open or closed, local or cloud —
the registry is designed for a growing set. See `src/duckdb_vectors/embedders/`
for the hashing, fastembed, and Bedrock examples, and keep heavy dependencies
behind an optional extra in `pyproject.toml`.

## How do I add a chunker? {#chunker}

Implement the `Chunker` protocol — any object with a `chunk(text, source)`
method returning chunks with stable ids. See `src/duckdb_vectors/chunkers/` for
the markdown chunker, and pass your chunker to `index_folder` or the store.

## What are the documentation conventions? {#docs-conventions}

Documentation lives in `documentation/`, GitHub-Flavored Markdown with mermaid
(no ASCII art), ATX sentence-case headers, and fenced code blocks. Two naming
conventions keep the set expandable:

- __Platform pages carry a provider prefix__ — `aws-bedrock-agentcore-guide.md`. Other platforms follow the same shape with their own prefix (`gcp-`, `azure-`, `modal-`), typically a `-guide.md` (teach-by-example) and a -workbook.md` (FAQ) pair.
- __Use-case pages are step-by-step procedures__ named `setup-{capability}-procedure.md`. One page per use case keeps the set growing without inflating any single page.

## How do I open a good pull request? {#pull-request}

- Keep each pull request focused on a single change.
- Write a clear description: what changed and why.
- Match the surrounding style; Ruff decides formatting.
- Update the README or docstrings when you change behavior.
- Run `make lint` and `make test` first; both should pass.
- Be kind in review.

## How do I report an issue? {#issues}

Open an issue with what you expected and what happened, a minimal snippet or
steps to reproduce, and your Python version plus how the store was configured
(backend, read-only).

## What are the prerequisites? {#prerequisites}

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

## What does each Makefile target do? {#makefile}

The `Makefile` consolidates the most frequent commands used throughout this project. Run `make` with no target to list them. The essentials:

- `make setup` — check prerequisites, create the `.venv`, install tooling
- `make lint` — Ruff, mypy, Markdown, and Dockerfile checks
- `make test` — the pytest suite
- `make format` — apply Ruff formatting
- `make push` — rebase onto `main`, then push
- `make build` — build the wheel and sdist with uv
- `make clean` — remove build artifacts and caches (including `*.out`)

## How do I set up a development environment? {#setup}

```shell
$ git clone https://github.com/bernard357/duckdb-vectors.git
$ cd duckdb-vectors
$ make setup   # checks prerequisites, creates the venv, installs tooling
```

`make setup` runs `check-prerequisites`, then `setup-python` (uv venv + the package with all extras) and `setup-markdownlint` (npm). `setup-hadolint` checks whether hadolint is present and, if not, points you at the official install guide — hadolint has no pip or npm package, so install it for your platform (binary release, Docker image, or a native package). Run `make setup-python` for just the Python environment if you manage the linters yourself.

## What does the lint cover? {#lint}

```shell
$ make lint        # ruff check + ruff format --check + mypy, markdownlint, hadolint
```

Python (Ruff lint and format, plus mypy type checking over `src/duckdb_vectors`
via `[tool.mypy]`), Markdown (markdownlint via `.markdownlint.json`), and
Dockerfiles (hadolint via `.hadolint.yaml`). mypy verifies the type annotations
the package ships behind its `py.typed` marker. Use `make format` to apply
Python formatting. All checks should pass before you open a pull request.

## How do I run the tests? {#tests}

```shell
$ make test        # pytest -q
```

The suite uses the dependency-free `hashing` backend, so it needs no model
download and no network and runs in a few seconds. Add a test for any behavior
you change or add; the suite under `tests/` shows the patterns (temp-file
DuckDB fixtures, deterministic embeddings).
