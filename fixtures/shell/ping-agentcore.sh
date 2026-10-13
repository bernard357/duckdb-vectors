#!/usr/bin/env bash
# End-to-end ping: deploy the RAG agent to Bedrock AgentCore and validate.
#
# Deploys the execution-role stack, host-indexes the documentation with Titan
# into the agent directory, then configures, deploys, and invokes the agent via
# the agentcore starter-toolkit CLI, piping the result to the shared validator.
# Resources are created and left running. Run from the repository root
# (via `make ping-agentcore`) with AWS credentials (AWS_PROFILE=bernard357).
set -euo pipefail

REGION="${AWS_REGION:-us-east-1}"
STACK_NAME="duckdb-vectors-ping-agentcore"
ROLE_NAME="duckdb-vectors-ping-agentcore-role"
AGENT_NAME="duckdb_vectors_ping"
# examples/agentcore-rag-agent is the pristine template; the deployable agent
# is assembled into a transient *.out directory so no tracked file is mutated.
TEMPLATE_DIR="examples/agentcore-rag-agent"
BUILD_DIR="ping-agentcore.out"
QUERY="how is a search answered?"
export AWS_DEFAULT_REGION="${REGION}"
export AGENTCORE_SUPPRESS_RECOMMENDATION=1

# The Python starter-toolkit CLI, not an unrelated npm `agentcore` on PATH.
# It is a ping-only dependency, not part of `make setup`; install it with
# `uv pip install --python .venv/bin/python bedrock-agentcore-starter-toolkit`.
AGENTCORE="$(pwd)/.venv/bin/agentcore"
if [ ! -x "${AGENTCORE}" ]; then
    echo "[agentcore] ${AGENTCORE} not found. Install the starter toolkit:"
    echo "  uv pip install --python .venv/bin/python bedrock-agentcore \\"
    echo "    bedrock-agentcore-starter-toolkit strands-agents"
    exit 1
fi

echo "[agentcore] deploying the execution-role stack..."
aws cloudformation deploy \
    --region "${REGION}" \
    --stack-name "${STACK_NAME}" \
    --template-file fixtures/cloudformation/ping-agentcore.yaml \
    --capabilities CAPABILITY_NAMED_IAM \
    --parameter-overrides "RoleName=${ROLE_NAME}"
ROLE_ARN="$(aws cloudformation describe-stacks --region "${REGION}" \
    --stack-name "${STACK_NAME}" \
    --query "Stacks[0].Outputs[?OutputKey=='RoleArn'].OutputValue" --output text)"

echo "[agentcore] assembling the agent into ${BUILD_DIR}/ (transient)..."
uv build
WHEEL="$(ls -t dist/*.whl | head -1)"
rm -rf "${BUILD_DIR}"
mkdir -p "${BUILD_DIR}"
cp "${TEMPLATE_DIR}/agent.py" "${BUILD_DIR}/"
cp "${WHEEL}" "${BUILD_DIR}/"
# The deployable requirements point at the local wheel (the package is not yet
# on PyPI); this file lives only in the transient build dir, never in the tree.
printf 'bedrock-agentcore\n./%s[bedrock]\n' "$(basename "${WHEEL}")" > "${BUILD_DIR}/requirements.txt"
cp -R documentation "${BUILD_DIR}/documentation"
python scripts/ping_host_index.py "${BUILD_DIR}/documentation" "${BUILD_DIR}/vectors.duckdb"

echo "[agentcore] configure / deploy / invoke..."
( cd "${BUILD_DIR}" && "${AGENTCORE}" configure -e agent.py -n "${AGENT_NAME}" \
    -er "${ROLE_ARN}" -r "${REGION}" --non-interactive )
( cd "${BUILD_DIR}" && "${AGENTCORE}" deploy --auto-update-on-conflict )
( cd "${BUILD_DIR}" && "${AGENTCORE}" invoke "{\"question\": \"${QUERY}\"}" ) \
    | python scripts/ping_validate.py agentcore

echo "[agentcore] resources left running: stack '${STACK_NAME}' and runtime '${AGENT_NAME}'."
echo "[agentcore] delete with:"
echo "  ( cd ${BUILD_DIR} && ${AGENTCORE} destroy )"
echo "  aws cloudformation delete-stack --region ${REGION} --stack-name ${STACK_NAME}"
