# AWS Lambda workbook

A FAQ for the non-agentic Lambda pattern — question in, grounded answer out.
For the walk-through, see the [Lambda guide](aws-lambda-guide.md).

- [Why container image, not a zip?](#image)
- [Where does the index live, and is /tmp needed?](#index-location)
- [How do I keep cold starts low?](#cold-start)
- [What permissions are required?](#permissions)
- [When should I use Lambda instead of AgentCore?](#vs-agentcore)
- [Follow-up](#follow-up)

## Why container image, not a zip? {#image}

The index is baked at build time, which is natural in a Dockerfile and sidesteps
the 250 MB unzipped limit of zip-based Lambdas (the `local` fastembed model plus
the DuckDB engine fit more comfortably in a container image, up to 10 GB). A zip
deploy is possible if you keep the dependency set small (for example the
`hashing` backend) and fetch the index from S3 to `/tmp` at startup.

## Where does the index live, and is /tmp needed? {#index-location}

Baked into the image at `${LAMBDA_TASK_ROOT}`, opened read-only. Because the
default ranking is a plain cosine scan with no persisted vector index, the
read-only task root is enough — you do not need `/tmp`. You only need `/tmp` if
you fetch or rebuild the index at runtime, since the task root is read-only.

## How do I keep cold starts low? {#cold-start}

- Open the `VectorStore` at module scope so it is created once per execution
  environment and reused across warm invocations.
- With the `local` backend, the fastembed model loads lazily on first embed;
  run a warm-up `query` at module load so the first real request does not pay
  for it.
- The `hashing` backend has no load cost but is not semantic; `bedrock:titan`
  adds a network round-trip per query but removes the model from the image.
- Keep `memory-size` generous enough — more memory also means more CPU, which
  speeds both the model load and the scan.

## What permissions are required? {#permissions}

`duckdb-vectors` reads a local file and needs no AWS permissions. The function's
execution role needs `bedrock:InvokeModel` on the answer model (and on the Titan
embed model if you embed at runtime). All credentials come from the execution
context via the default boto3 chain — the same inherit-from-the-role model used
on AgentCore, ECS, and EC2.

## When should I use Lambda instead of AgentCore? {#vs-agentcore}

Use Lambda when the interaction is a single turn: one question, one retrieval,
one model call, one answer — no multi-step tool use, no session memory. It is
the cheapest, simplest shape and scales to zero. Reach for
[AgentCore](aws-bedrock-agentcore-guide.md) when you need the agentic loop:
multi-turn sessions, tool orchestration, and managed memory.

## Follow-up {#follow-up}

- [aws-lambda-guide.md](aws-lambda-guide.md) — the walk-through
- [aws-bedrock-agentcore-workbook.md](aws-bedrock-agentcore-workbook.md) — the agentic path
- [duckdb-vectors-guide.md](duckdb-vectors-guide.md) — harness integration
