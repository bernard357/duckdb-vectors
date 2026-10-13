"""End-to-end ping: deploy the RAG agent to AWS Lambda and validate a search.

Builds a Lambda container image from ``documentation/`` (Titan Embed for the
index at build time, Nova Pro via the Converse API for answer assembly at
invocation), pushes it to ECR, deploys the CloudFormation stack, invokes the
function with the fixed query, and asserts the expected source and a grounded
answer. Resources are created and left running; a cleanup hint is printed.

Prerequisites: Docker, the AWS CLI, and credentials (``AWS_PROFILE=bernard357``).

Usage::

    $ AWS_PROFILE=bernard357 python scripts/ping_lambda.py
"""

import base64
import json
import os
import subprocess
import sys
import time

import boto3
from ping_support import (
    AWS_REGION,
    PING_QUERY,
    REPO_ROOT,
    assert_answer_grounded,
    assert_sources_contain_expected,
    report,
)

ECR_REPO = "duckdb-vectors-ping-lambda"
IMAGE_TAG = "latest"
STACK_NAME = "duckdb-vectors-ping-lambda"
FUNCTION_NAME = "duckdb-vectors-ping-lambda"
TEMPLATE = REPO_ROOT / "fixtures" / "cloudformation" / "ping-lambda.yaml"


def _run(cmd, **kwargs):
    print(f"[lambda] $ {' '.join(cmd)}")
    return subprocess.run(cmd, check=True, cwd=REPO_ROOT, **kwargs)


def ensure_ecr(session) -> str:
    """Create the ECR repository if absent; return the registry URI."""
    ecr = session.client("ecr", region_name=AWS_REGION)
    try:
        ecr.create_repository(repositoryName=ECR_REPO)
    except ecr.exceptions.RepositoryAlreadyExistsException:
        pass
    account = session.client("sts").get_caller_identity()["Account"]
    return f"{account}.dkr.ecr.{AWS_REGION}.amazonaws.com"


def docker_login(session, registry: str) -> None:
    ecr = session.client("ecr", region_name=AWS_REGION)
    token = ecr.get_authorization_token()["authorizationData"][0]
    user, password = (
        base64.b64decode(token["authorizationToken"]).decode().split(":", 1)
    )
    subprocess.run(
        ["docker", "login", "-u", user, "--password-stdin", registry],
        check=True,
        input=password,
        text=True,
    )


def build_index_on_host() -> None:
    """Index the documentation with Titan on the host, writing vectors.duckdb.

    Indexing runs here, not in the Docker build, so AWS credentials never enter
    an image layer. The finished index is baked into the image.
    """
    from duckdb_vectors import VectorStore, index_folder

    db_path = REPO_ROOT / "vectors.duckdb"
    if db_path.exists():
        db_path.unlink()
    store = VectorStore(db_path=str(db_path), backend="bedrock:titan")
    try:
        rep = index_folder(
            store, str(REPO_ROOT / "documentation"), recursive=True
        )
        print(f"[lambda] host-indexed {rep.files} files, {rep.chunks} chunks")
    finally:
        store.close()


def build_and_push(session, registry: str) -> str:
    """Build the wheel and image, push to ECR, return the image URI."""
    _run(["uv", "build"])
    image_uri = f"{registry}/{ECR_REPO}:{IMAGE_TAG}"
    _run(
        [
            "docker",
            "build",
            "--platform",
            "linux/amd64",
            "-f",
            "examples/lambda-rag-agent/Dockerfile",
            "-t",
            image_uri,
            ".",
        ]
    )
    docker_login(session, registry)
    _run(["docker", "push", image_uri])
    return image_uri


def deploy_stack(session, image_uri: str) -> None:
    cfn = session.client("cloudformation", region_name=AWS_REGION)
    _run(
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
            f"ImageUri={image_uri}",
            f"FunctionName={FUNCTION_NAME}",
        ]
    )
    cfn.get_waiter("stack_create_complete")  # no-op if already exists
    # Ensure the function picked up the (possibly new) image.
    lam = session.client("lambda", region_name=AWS_REGION)
    lam.update_function_code(
        FunctionName=FUNCTION_NAME, ImageUri=image_uri, Publish=True
    )
    lam.get_waiter("function_updated").wait(FunctionName=FUNCTION_NAME)


def invoke(session) -> dict:
    lam = session.client("lambda", region_name=AWS_REGION)
    # Cold start plus Nova call can take a while; retry a couple of times.
    for attempt in range(3):
        resp = lam.invoke(
            FunctionName=FUNCTION_NAME,
            Payload=json.dumps({"question": PING_QUERY}).encode(),
        )
        payload = json.loads(resp["Payload"].read())
        if "FunctionError" not in resp:
            return payload
        print(f"[lambda] invoke error (attempt {attempt + 1}): {payload}")
        time.sleep(5)
    raise AssertionError(f"lambda invocation failed: {payload}")


def main() -> int:
    os.environ.setdefault("AWS_DEFAULT_REGION", AWS_REGION)
    session = boto3.Session(region_name=AWS_REGION)
    build_index_on_host()
    registry = ensure_ecr(session)
    image_uri = build_and_push(session, registry)
    deploy_stack(session, image_uri)
    result = invoke(session)
    sources = result.get("sources", [])
    answer = result.get("answer", "")
    report("lambda", sources, answer)
    assert_sources_contain_expected(sources)
    assert_answer_grounded(answer)
    print("[lambda] PASS")
    print(
        f"[lambda] resources left running: stack '{STACK_NAME}', ECR repo "
        f"'{ECR_REPO}'. Delete with:\n"
        f"  aws cloudformation delete-stack --region {AWS_REGION} "
        f"--stack-name {STACK_NAME}\n"
        f"  aws ecr delete-repository --region {AWS_REGION} "
        f"--repository-name {ECR_REPO} --force"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
