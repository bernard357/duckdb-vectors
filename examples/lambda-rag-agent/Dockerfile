# AWS Lambda RAG agent over the duckdb-vectors documentation.
#
# Builds on the AWS Lambda Python base image. The index is embedded with Amazon
# Titan Embed on the host (ping_lambda.py builds it with the caller's AWS
# credentials, so none leak into the image) and baked in here; it is opened
# at run time. Nova Pro assembles the answer at invocation via the
# Bedrock Converse API, using the function's execution-role credentials.
#
# Build and push happen in scripts/ping_lambda.py, which first writes
# vectors.duckdb at the repo root with the Titan backend.

FROM public.ecr.aws/lambda/python:3.12

# duckdb-vectors is not yet on PyPI; install it from the locally built wheel.
COPY dist/ ${LAMBDA_TASK_ROOT}/dist/
RUN pip install --no-cache-dir "$(echo ${LAMBDA_TASK_ROOT}/dist/*.whl)[bedrock]" \
    && rm -rf ${LAMBDA_TASK_ROOT}/dist

# Bake the pre-built index (host-indexed with Titan — no credentials in build).
COPY vectors.duckdb ${LAMBDA_TASK_ROOT}/vectors.duckdb
COPY examples/lambda-rag-agent/handler.py ${LAMBDA_TASK_ROOT}/
ENV DUCKDB_VECTORS_DB_PATH=${LAMBDA_TASK_ROOT}/vectors.duckdb
CMD ["handler.lambda_handler"]
