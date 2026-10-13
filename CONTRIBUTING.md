# Contributing to duckdb-vectors

Thanks for considering a contribution. `duckdb-vectors` grows through small,
focused improvements, and new contributors are genuinely welcome — whether you
fix a typo, add an embedding backend, or sharpen the docs.

This project is intentionally small. The best contributions keep it that way:
one clear thing, done well, with a test and a line of docs.

## Good first contributions

You do not need to understand the whole codebase to help. Some low-friction,
high-value starting points:

- __Add an embedding backend.__ The embedder registry is the main extension
  point. A backend is a small class with `model_identifier`, `dimensions`, and
  `embed(texts)`, registered with `register_embedder(name, factory)`. Open or
  closed, local or cloud — the registry is designed for a growing set. See
  `src/duckdb_vectors/embedders/` for the hashing, fastembed, and Bedrock
  examples.
- __Add a chunker.__ Implement the `Chunker` protocol (`chunk(text, source)`)
  for a format we do not cover well yet. See
  `src/duckdb_vectors/chunkers/`.
- __Improve the docs.__ Clarify the README, add a worked example under
  `examples/`, or document a pattern you found useful.
- __Share a ranking recipe.__ A neat injectable `vector_sql` / `text_sql` for
  scoping or decay that others could reuse.
- __Report a rough edge.__ A clear issue with a minimal reproduction is a real
  contribution.

If you are unsure where to start, open an issue describing what you want to do
and we will point you at the right spot.

## Project principles

Contributions land fastest when they fit the project's shape:

- __Do one thing well.__ This is a retrieval building block — store, index,
  search. Generation, prompting, and LLM orchestration belong in the harness,
  not here.
- __Boring, composable technology.__ Prefer the standard library and DuckDB's
  own features over new dependencies. New runtime dependencies need a good
  reason; heavy ones belong behind an optional extra.
- __Framework-agnostic.__ No hard dependency on a specific agent framework. The
  component should drop into any of them.
- __Read-mostly and fast.__ The serving path opens read-only and answers in
  tens of milliseconds. Keep that budget in mind.

## Development setup

The project uses [uv](https://docs.astral.sh/uv/), ruff, and pytest.

```shell
$ git clone https://github.com/bernard357/duckdb-vectors.git
$ cd duckdb-vectors
$ uv venv
$ uv pip install -e ".[local,bedrock,cli,dev]"
```

## Checks before you open a pull request

```shell
$ uv run ruff format .        # format
$ uv run ruff check .         # lint
$ uv run pytest -q            # tests
```

All three should pass. Add a test for any behavior you change or add — the
suite under `tests/` shows the patterns (temp-file DuckDB fixtures, the
dependency-free `hashing` backend so tests need no model download).

## Pull request guidelines

- Keep each pull request focused on a single change.
- Write a clear description: what changed and why.
- Match the surrounding style; ruff decides formatting.
- Update the README or docstrings when you change behavior.
- Be kind in review. We are all here to make the tool better.

## Reporting issues

Open an issue with:

- what you expected and what happened,
- a minimal snippet or steps to reproduce,
- your Python version and how the store was configured (backend, read-only).

## License

By contributing, you agree that your contributions are licensed under the
[MIT License](LICENSE) that covers this project.
