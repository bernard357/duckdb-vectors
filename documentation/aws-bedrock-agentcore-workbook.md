# AWS Bedrock AgentCore workbook

A FAQ for running `duckdb-vectors` inside an Amazon Bedrock AgentCore agent.
For the walk-through, see the [AgentCore guide](aws-bedrock-agentcore-guide.md).

- [Where does the index live?](#index-location)
- [What permissions does duckdb-vectors need?](#permissions)
- [Local index or runtime embedding?](#local-or-runtime)
- [How do I refresh the index without redeploying?](#refresh)
- [How does this differ from a plain container?](#vs-container)
- [Follow-up](#follow-up)

## Where does the index live? {#index-location}

Inside the deployment artifact, as a local DuckDB file. The agent opens it
read-only at module load and retrieves per request. Nothing leaves the runtime
for retrieval — the scan is in-process and answers in tens of milliseconds.

## What permissions does duckdb-vectors need? {#permissions}

None of its own for reading the baked-in index — it reads a local file. All AWS
permissions are __inherited from the execution context__ of the container that
AgentCore runs: the agent's execution role, which the starter toolkit
provisions during `agentcore configure` / `deploy`.

That means `duckdb-vectors` makes no assumption about credentials. When a part
of the agent needs AWS access, it uses the ambient role:

- The model calls that generate answers need `bedrock:InvokeModel` on the model.
- If you embed at runtime with `bedrock:titan` (rather than a baked local
  index), the embedder's boto3 client uses the same ambient role, which must
  allow `bedrock:InvokeModel` on the Titan embed model. You can also pass an
  explicit boto3 `session` to the embedder if you need different credentials.

This inherit-from-execution-context model is the same on any AWS compute
(AgentCore, Lambda, ECS, EC2): grant the role, and `duckdb-vectors` picks up
credentials through the default boto3 chain.

## Local index or runtime embedding? {#local-or-runtime}

- __Local index (recommended).__ Index with `local` (fastembed) at build time
  and bake the `.duckdb` into the artifact. Retrieval is fully offline inside
  the runtime; the only Bedrock calls are the model calls that write answers.
- __Runtime embedding with `bedrock:titan`.__ The query embedding is computed
  by a Bedrock call per request. This removes the fastembed dependency from the
  image but adds a network round-trip to each query and requires the execution
  role to allow the Titan embed model. The stored index must have been built
  with the same backend.

## How do I refresh the index without redeploying? {#refresh}

Fetch a prebuilt `.duckdb` from S3 at agent startup into the runtime's writable
area, then open it read-only. Re-fetch on an interval for long-lived runtimes.
This trades a bounded stale-read window for not rebuilding the artifact on
every corpus change. If the corpus changes on your release cadence, prefer
re-indexing at build time instead.

## How does this differ from a plain container? {#vs-container}

The `duckdb-vectors` usage is identical — open read-only, `query`, feed the
model. What differs is the harness and deployment: AgentCore provides the
managed runtime, the entrypoint contract (`@app.entrypoint`), the execution
role, memory options, and observability, and the toolkit builds and deploys the
artifact for you. A plain container (see the
[Docker agent guide](docker-agent-guide.md)) gives you more control and runs
anywhere, at the cost of operating the runtime yourself.

## Follow-up {#follow-up}

- [aws-bedrock-agentcore-guide.md](aws-bedrock-agentcore-guide.md) — the walk-through
- [aws-lambda-workbook.md](aws-lambda-workbook.md) — the non-agentic AWS path
- [AgentCore permissions](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/runtime-permissions.html)
