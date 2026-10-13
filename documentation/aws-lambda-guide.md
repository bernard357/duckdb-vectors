# AWS Lambda guide

A teach-by-example walk-through for the simplest useful shape: a Lambda
function that takes a question as input and returns a grounded answer as
output. No agentic loop — one retrieval, one Bedrock call, one response. This
is RAG reduced to a stateless function.

- [What you will build](#what)
- [Step 1 — bake the index into the image](#index)
- [Step 2 — the handler](#handler)
- [Step 3 — package and deploy](#deploy)
- [Permissions](#permissions)
- [Follow-up](#follow-up)

## What you will build {#what}

A container-image Lambda whose `/invoke` takes `{"question": "..."}` and
returns `{"answer": "..."}`. The documentation index is baked into the image;
the handler retrieves chunks with `duckdb-vectors` and asks a Bedrock model to
answer from them.

```mermaid
flowchart LR
    Event["{question}"] --> Handler[Lambda handler]
    DB[(vectors.duckdb in image)] --> Store[VectorStore read-only]
    Handler --> Store
    Store --> Context[top-k chunks]
    Context --> Bedrock[bedrock InvokeModel]
    Bedrock --> Response["{answer}"]
```

## Step 1 — bake the index into the image {#index}

Lambda container images are read-only except for `/tmp`. Bake the index at
build time and open it read-only — no writable path needed:

```dockerfile
FROM public.ecr.aws/lambda/python:3.12
RUN pip install duckdb-vectors[local,bedrock]
COPY documentation/ ${LAMBDA_TASK_ROOT}/documentation/
RUN duckdb-vectors index ${LAMBDA_TASK_ROOT}/documentation \
    --db ${LAMBDA_TASK_ROOT}/vectors.duckdb --backend local
COPY handler.py ${LAMBDA_TASK_ROOT}/
CMD ["handler.lambda_handler"]
```

## Step 2 — the handler {#handler}

Open the store once at module load (reused across warm invocations), retrieve
per request, and make a single direct Bedrock call to generate the answer:

```python
import json

import boto3

from duckdb_vectors import VectorStore

store = VectorStore(
    db_path="vectors.duckdb", backend="local", read_only=True
)
bedrock = boto3.client("bedrock-runtime")
MODEL_ID = "anthropic.claude-3-5-sonnet-20240620-v1:0"


def lambda_handler(event, context):
    question = event.get("question", "")
    hits = store.query(question, top_k=5)
    grounding = "\n\n".join(f"[{h.chunk.source}] {h.chunk.text}" for h in hits)

    body = {
        "anthropic_version": "bedrock-2023-05-31",
        "max_tokens": 512,
        "messages": [
            {
                "role": "user",
                "content": (
                    f"Answer using only this context:\n\n{grounding}\n\n"
                    f"Question: {question}"
                ),
            }
        ],
    }
    response = bedrock.invoke_model(modelId=MODEL_ID, body=json.dumps(body))
    payload = json.loads(response["body"].read())
    answer = payload["content"][0]["text"]
    return {"answer": answer, "sources": [h.chunk.source for h in hits]}
```

The store is created at module scope so the index is opened once per execution
environment and reused across warm invocations. Trigger a warm-up query if you
use the `local` backend, so the first real request does not pay the one-time
model load.

## Step 3 — package and deploy {#deploy}

Build the image, push to ECR, and create the function from it:

```shell
$ docker build -t duckdb-vectors-lambda .
$ aws ecr create-repository --repository-name duckdb-vectors-lambda
$ # docker tag + push to the ECR repository URI, then:
$ aws lambda create-function \
    --function-name duckdb-vectors-rag \
    --package-type Image \
    --code ImageUri=<account>.dkr.ecr.<region>.amazonaws.com/duckdb-vectors-lambda:latest \
    --role arn:aws:iam::<account>:role/<lambda-execution-role> \
    --timeout 30 --memory-size 1024
```

## Permissions {#permissions}

`duckdb-vectors` reads the baked-in file and needs no AWS permissions. The
function's __execution role__ must allow the one Bedrock call that generates
the answer: `bedrock:InvokeModel` on the chosen model (and on the Titan embed
model too, if you switch to runtime `bedrock:titan` embedding instead of a
baked local index). Credentials are picked up from the execution context
through the default boto3 chain.

## Follow-up {#follow-up}

- [aws-lambda-workbook.md](aws-lambda-workbook.md) — FAQ for the Lambda pattern
- [aws-bedrock-agentcore-guide.md](aws-bedrock-agentcore-guide.md) — the agentic AWS path
- [setup-rag-procedure.md](setup-rag-procedure.md) — the full RAG procedure
