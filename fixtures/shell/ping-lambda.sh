#!/usr/bin/env bash
# End-to-end ping: deploy the RAG agent to AWS Lambda and validate a search.
#
# Host-indexes the documentation with Titan (no credentials in the image),
# builds and pushes the image to ECR, deploys the CloudFormation stack, invokes
# the function, and pipes the result to the shared validator. Resources are
# created and left running. Run from the repository root (via `make ping-lambda`)
# with AWS credentials (AWS_PROFILE=bernard357).
set -euo pipefail

REGION="${AWS_REGION:-us-east-1}"
ECR_REPO="duckdb-vectors-ping-lambda"
IMAGE_TAG="latest"
STACK_NAME="duckdb-vectors-ping-lambda"
FUNCTION_NAME="duckdb-vectors-ping-lambda"
QUERY="how is a search answered?"
export AWS_DEFAULT_REGION="${REGION}"

ACCOUNT="$(aws sts get-caller-identity --query Account --output text)"
REGISTRY="${ACCOUNT}.dkr.ecr.${REGION}.amazonaws.com"
IMAGE_URI="${REGISTRY}/${ECR_REPO}:${IMAGE_TAG}"

echo "[lambda] host-indexing the documentation with Titan..."
python scripts/ping_host_index.py documentation vectors.duckdb

echo "[lambda] building the wheel and image..."
uv build
docker build --platform linux/amd64 -f examples/lambda-rag-agent/Dockerfile -t "${IMAGE_URI}" .

echo "[lambda] pushing to ECR..."
aws ecr describe-repositories --repository-names "${ECR_REPO}" --region "${REGION}" >/dev/null 2>&1 \
    || aws ecr create-repository --repository-name "${ECR_REPO}" --region "${REGION}" >/dev/null
aws ecr get-login-password --region "${REGION}" \
    | docker login -u AWS --password-stdin "${REGISTRY}"
docker push "${IMAGE_URI}"

echo "[lambda] deploying the stack..."
aws cloudformation deploy \
    --region "${REGION}" \
    --stack-name "${STACK_NAME}" \
    --template-file fixtures/cloudformation/ping-lambda.yaml \
    --capabilities CAPABILITY_NAMED_IAM \
    --parameter-overrides "ImageUri=${IMAGE_URI}" "FunctionName=${FUNCTION_NAME}"
aws lambda update-function-code \
    --region "${REGION}" --function-name "${FUNCTION_NAME}" \
    --image-uri "${IMAGE_URI}" --publish >/dev/null
aws lambda wait function-updated --region "${REGION}" --function-name "${FUNCTION_NAME}"

echo "[lambda] invoking the function..."
printf '{"question": "%s"}' "${QUERY}" > /tmp/ping-lambda-payload.json
aws lambda invoke \
    --region "${REGION}" --function-name "${FUNCTION_NAME}" \
    --cli-binary-format raw-in-base64-out \
    --payload "file:///tmp/ping-lambda-payload.json" \
    /tmp/ping-lambda-response.json >/dev/null
python scripts/ping_validate.py lambda < /tmp/ping-lambda-response.json

echo "[lambda] resources left running: stack '${STACK_NAME}', ECR repo '${ECR_REPO}'."
echo "[lambda] delete with:"
echo "  aws cloudformation delete-stack --region ${REGION} --stack-name ${STACK_NAME}"
echo "  aws ecr delete-repository --region ${REGION} --repository-name ${ECR_REPO} --force"
