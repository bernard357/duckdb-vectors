# Docker agent workbook

A FAQ for packaging `duckdb-vectors` into a container agent. For the full
walk-through, see the [Docker agent guide](docker-agent-guide.md).

- [Why bake the index into the image?](#why-bake)
- [Which embedding backend in a container?](#backend)
- [How do I keep the image small?](#image-size)
- [How do I refresh the index?](#refresh)
- [Can the container write at runtime?](#runtime-write)
- [What about cold start and latency?](#latency)
- [Follow-up](#follow-up)

## Why bake the index into the image? {#why-bake}

A baked-in index makes the container self-contained: no external vector
database, no network dependency at query time, and a reproducible artifact that
is identical in every environment. The build-then-freeze lifecycle opens the
file read-only at run time, which needs no experimental persistence flags
because the default ranking is a plain cosine scan.

The alternative — fetching an index from object storage at startup — makes
sense when the corpus changes faster than you redeploy. See
[How do I refresh the index?](#refresh).

## Which embedding backend in a container? {#backend}

- `local` (fastembed) — ONNX, no torch, 384 dims. The default and the minimum
  viable setup for real relevance; the model is downloaded at build time and
  baked in, so the container runs offline.
- `bedrock:titan` — embeds through Amazon Bedrock. The index stays local; only
  embedding calls go to AWS. Needs credentials at the step that embeds (build
  time if you index during build, run time if you index at startup).
- `hashing` — no dependency, no model, deterministic, but not semantically
  meaningful. Use it only for tests and CI smoke runs, not for a real agent.

The backend identity is folded into each chunk's ETag, so an index built with
one backend must be queried with the same backend.

## How do I keep the image small? {#image-size}

- Use a two-stage build: install and index in the builder stage, copy only the
  agent code and the frozen `.duckdb` into the runtime stage.
- Prefer `python:3.12-slim` as the base.
- The `hashing` backend adds nothing; `local` pulls fastembed and an ONNX model
  (tens of MB); `bedrock` pulls boto3. Install only the extra you use.
- The DuckDB file itself is compact — a few thousand 384-dim chunks is a small
  number of megabytes.

## How do I refresh the index? {#refresh}

Two patterns:

- __Rebuild the image.__ If the corpus changes on your release cadence,
  re-index during the next `docker build`. Simplest and fully reproducible.
- __Fetch at startup.__ If the corpus changes faster than you deploy, download
  a prebuilt `.duckdb` from object storage into the container at startup and
  open it read-only. Re-download on an interval if the process is long-lived.

Deleting a source file is handled by `index_folder`: chunks whose run stamp is
older than the current run are pruned, so a rebuild drops removed pages.

## Can the container write at runtime? {#runtime-write}

Yes, for the runtime-append pattern. Open the store read-write at startup and
append a registry (for example a set of tools or skills), then search it. This
is distinct from the frozen-docs case: a writable DuckDB file is single-writer,
so keep writes to one process. For a read-only frozen index, open with
`read_only=True`, which also refuses accidental writes. See
[setup-skills-search-procedure.md](setup-skills-search-procedure.md).

## What about cold start and latency? {#latency}

Total query latency is the query embedding plus the ranking scan. The scan over
a few-to-tens-of-thousands of 384-dim chunks is single-digit to low-tens of
milliseconds. With `local` embedding, the one-time model load happens on first
use — trigger a warm-up query at startup so the first real request is not the
one that pays for it. `hashing` has no load cost; `bedrock` adds a network
round-trip per embed.

## Follow-up {#follow-up}

- [docker-agent-guide.md](docker-agent-guide.md) — the teach-by-example build
- [duckdb-vectors-primitive.md](duckdb-vectors-primitive.md) — lifecycles and architecture
- [aws-bedrock-agentcore-workbook.md](aws-bedrock-agentcore-workbook.md) — deploying the container on AWS
