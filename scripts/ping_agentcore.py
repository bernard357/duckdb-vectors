"""End-to-end ping: deploy the RAG agent to Bedrock AgentCore and validate.

Deploys an answering agent on Amazon Bedrock AgentCore via the agentcore
starter-toolkit CLI (configure, deploy, invoke), using an execution role from
CloudFormation. The documentation index is embedded with Amazon Titan Embed on
the host and baked alongside the agent; Nova Pro assembles the answer via the
Bedrock Converse API at invocation. Resources are created and left running; a
cleanup hint is printed.

Prerequisites: Docker, the AWS CLI, the agentcore CLI
(``pip install bedrock-agentcore-starter-toolkit``), and credentials
(``AWS_PROFILE=bernard357``).

Usage::

    $ AWS_PROFILE=bernard357 python scripts/ping_agentcore.py
"""

import json
import os
import shutil
import subprocess
import sys

import boto3
from ping_support import (
    AWS_REGION,
    PING_QUERY,
    REPO_ROOT,
    assert_answer_grounded,
    assert_sources_contain_expected,
    report,
)

STACK_NAME = "duckdb-vectors-ping-agentcore"
ROLE_NAME = "duckdb-vectors-ping-agentcore-role"
AGENT_NAME = "duckdb_vectors_ping"
AGENT_DIR = REPO_ROOT / "examples" / "agentcore-rag-agent"
TEMPLATE = REPO_ROOT / "fixtures" / "cloudformation" / "ping-agentcore.yaml"


def _run(cmd, **kwargs):
    print(f"[agentcore] $ {' '.join(str(c) for c in cmd)}")
    return subprocess.run(cmd, check=True, cwd=AGENT_DIR, **kwargs)


def deploy_role(session) -> str:
    """Create the execution-role stack; return the role ARN."""
    subprocess.run(
        [
            "aws",
            "cloudformation",
            "deploy",
            "--region",
            AWS_REGION,
            "--stack-name",
            STACK_NAME,
            "--template-file",
            str(TEMPLATE),
            "--capabilities",
            "CAPABILITY_NAMED_IAM",
            "--parameter-overrides",
            f"RoleName={ROLE_NAME}",
        ],
        check=True,
        cwd=REPO_ROOT,
    )
    cfn = session.client("cloudformation", region_name=AWS_REGION)
    outputs = cfn.describe_stacks(StackName=STACK_NAME)["Stacks"][0]["Outputs"]
    return next(o["OutputValue"] for o in outputs if o["OutputKey"] == "RoleArn")


def stage_agent() -> None:
    """Build the wheel and stage it plus a Titan-embedded index in AGENT_DIR."""
    subprocess.run(["uv", "build"], check=True, cwd=REPO_ROOT)
    wheel = sorted((REPO_ROOT / "dist").glob("*.whl"))[-1]
    shutil.copy(wheel, AGENT_DIR / wheel.name)
    # Point the agent's requirements at the local wheel (not-yet-on-PyPI).
    (AGENT_DIR / "requirements.txt").write_text(
        f"bedrock-agentcore\n./{wheel.name}[bedrock]\n"
    )
    # Copy the documentation and host-index it with Titan.
    docs_dst = AGENT_DIR / "documentation"
    if docs_dst.exists():
        shutil.rmtree(docs_dst)
    shutil.copytree(REPO_ROOT / "documentation", docs_dst)

    from duckdb_vectors import VectorStore, index_folder

    db_path = AGENT_DIR / "vectors.duckdb"
    if db_path.exists():
        db_path.unlink()
    store = VectorStore(db_path=str(db_path), backend="bedrock:titan")
    try:
        rep = index_folder(store, str(docs_dst), recursive=True)
        print(f"[agentcore] host-indexed {rep.files} files, {rep.chunks} chunks")
    finally:
        store.close()


# The Python starter-toolkit CLI, not the unrelated npm `agentcore` that may
# shadow it on PATH. Resolve it next to the running interpreter.
AGENTCORE = str(REPO_ROOT / ".venv" / "bin" / "agentcore")


def deploy_agent(role_arn: str) -> None:
    _run(
        [
            AGENTCORE,
            "configure",
            "-e",
            "agent.py",
            "-n",
            AGENT_NAME,
            "-er",
            role_arn,
            "-r",
            AWS_REGION,
            "--non-interactive",
        ]
    )
    _run([AGENTCORE, "deploy"])


def invoke() -> dict:
    completed = _run(
        [AGENTCORE, "invoke", json.dumps({"question": PING_QUERY})],
        capture_output=True,
        text=True,
    )
    # The CLI prints a formatted banner, then "Response:" and the JSON payload
    # soft-wrapped across lines (literal newlines land inside string values).
    text = completed.stdout
    marker = text.rfind("Response:")
    if marker != -1:
        text = text[marker + len("Response:") :]
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1:
        raise AssertionError(f"no JSON in agentcore invoke output: {text}")
    # Collapse soft-wrap newlines inside the JSON payload.
    payload = " ".join(text[start : end + 1].split())
    return json.loads(payload)


def main() -> int:
    os.environ.setdefault("AWS_DEFAULT_REGION", AWS_REGION)
    os.environ.setdefault("AGENTCORE_SUPPRESS_RECOMMENDATION", "1")
    session = boto3.Session(region_name=AWS_REGION)
    role_arn = deploy_role(session)
    stage_agent()
    deploy_agent(role_arn)
    result = invoke()
    sources = result.get("sources", [])
    answer = result.get("answer", "")
    report("agentcore", sources, answer)
    assert_sources_contain_expected(sources)
    assert_answer_grounded(answer)
    print("[agentcore] PASS")
    print(
        f"[agentcore] resources left running: stack '{STACK_NAME}' and the "
        f"AgentCore runtime '{AGENT_NAME}'. Delete with:\n"
        f"  agentcore destroy\n"
        f"  aws cloudformation delete-stack --region {AWS_REGION} "
        f"--stack-name {STACK_NAME}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
