# Contributing to duckdb-vectors

Thanks for considering a contribution. `duckdb-vectors` grows through small,
focused improvements, and new contributors are genuinely welcome — whether you
fix a typo, add an embedding backend, or sharpen the docs.

This page helps you decide whether a change fits before you dive in. The full
developer reference — prerequisites, setup, the Makefile workflow, testing, and
the detailed conventions — lives in the
[developer workbook](documentation/developer-workbook.md).

## Does your change fit?

`duckdb-vectors` is intentionally small, and the best contributions keep it
that way: one clear thing, done well, with a test and a line of docs. A change
fits when it respects the project's shape:

- __Do one thing well.__ This is a retrieval building block — store, index,
  search. Generation, prompting, and LLM orchestration belong in the harness,
  not here.
- __Boring, composable technology.__ Prefer the standard library and DuckDB's
  own features over new dependencies; heavy ones belong behind an optional
  extra.
- __Framework-agnostic.__ No hard dependency on a specific agent framework.
- __Read-mostly and fast.__ The serving path opens read-only and answers in
  tens of milliseconds.

## Good first contributions

You do not need to understand the whole codebase to help:

- __Add an embedding backend__ — the registry is the main extension point.
- __Add a chunker__ — for a document format not covered well yet.
- __Improve the docs__ — clarify a page or add a worked example.
- __Share a ranking recipe__ — a neat injectable `vector_sql` / `text_sql`.
- __Report a rough edge__ — a clear issue with a minimal reproduction.

The [developer workbook](documentation/developer-workbook.md) explains how to do
each of these, how to set up your environment (`make setup`), and how to run the
checks (`make lint`, `make test`) before you open a pull request. If you are
unsure where to start, open an issue describing what you want to do.

## License

By contributing, you agree that your contributions are licensed under the
[MIT License](LICENSE) that covers this project.
