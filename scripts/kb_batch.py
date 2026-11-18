"""Run the queued model requests of a work folder as Bedrock batch jobs.

A curation script run with ``--batch`` answers what it can from earlier answers
and queues the rest in ``WORK/batch/pending.jsonl``. Batch inference has its own
quotas, well above the daily token quota of on-demand calls, and costs half as
much, at the price of a delay (Bedrock aims to finish a job within 24 hours).

    $ .venv/bin/python -m scripts.kb_batch submit --work DIR \\
        --bucket BUCKET --role-arn ARN
    $ .venv/bin/python -m scripts.kb_batch status --work DIR
    $ .venv/bin/python -m scripts.kb_batch fetch --work DIR

``fetch`` appends the answers of completed jobs to ``WORK/batch/answers.jsonl``;
then run the curation script again, with ``--batch``, to complete from them.
The bucket must be in the job's region; the role must let
``bedrock.amazonaws.com`` read and write it and invoke the model.
"""

import argparse
import datetime
import json
import sys
from pathlib import Path
from typing import Any

from scripts.kb_common import DEFAULT_MODEL, DEFAULT_REGION, read_json, write_json

MIN_RECORDS = 100  # Bedrock refuses smaller batch jobs
MAX_RECORDS = 50_000
MAX_BYTES = 900_000_000  # under the 1 GB input file limit
ANTHROPIC_VERSION = "bedrock-2023-05-31"


def record(request: dict) -> dict:
    """A batch record in the Anthropic Messages format Bedrock expects."""
    return {
        "recordId": request["key"],
        "modelInput": {
            "anthropic_version": ANTHROPIC_VERSION,
            "max_tokens": request["max_tokens"],
            "temperature": 0,
            "system": request["system"],
            "messages": [
                {
                    "role": "user",
                    "content": [{"type": "text", "text": t} for t in request["texts"]],
                }
            ],
        },
    }


def split_jobs(lines: list[str]) -> list[list[str]]:
    """Group records into jobs under the record and size limits."""
    jobs: list[list[str]] = [[]]
    size = 0
    for line in lines:
        if jobs[-1] and (len(jobs[-1]) >= MAX_RECORDS or size + len(line) > MAX_BYTES):
            jobs.append([])
            size = 0
        jobs[-1].append(line)
        size += len(line.encode()) + 1
    return [job for job in jobs if job]


def parse_output(text: str) -> tuple[list[dict], list[dict]]:
    """``(answers, errors)`` from a batch output file."""
    answers, errors = [], []
    for line in text.splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        output = row.get("modelOutput") or {}
        content = output.get("content") or []
        if row.get("error") or not content:
            errors.append({"key": row.get("recordId"), "error": row.get("error")})
            continue
        answers.append(
            {
                "key": row["recordId"],
                "text": content[0].get("text", "").strip(),
                "usage": output.get("usage", {}),
            }
        )
    return answers, errors


def jobs_file(work: Path) -> Path:
    return work / "batch" / "jobs.json"


def load_jobs(work: Path) -> list[dict]:
    path = jobs_file(work)
    return read_json(path) if path.exists() else []


def submit(
    work: Path,
    bucket: str,
    role_arn: str,
    model_id: str,
    region: str,
    session: Any = None,
) -> list[dict]:
    """Upload the pending requests not yet in a job and start the jobs."""
    import boto3

    session = session or boto3.Session()
    s3 = session.client("s3", region_name=region)
    bedrock = session.client("bedrock", region_name=region)
    pending_file = work / "batch" / "pending.jsonl"
    if not pending_file.exists():
        return []
    jobs = load_jobs(work)
    submitted = {
        key for job in jobs if job["status"] != "Failed" for key in job["keys"]
    }
    requests = [
        json.loads(line)
        for line in pending_file.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    fresh = [r for r in requests if r["key"] not in submitted]
    if not fresh:
        return []
    if len(fresh) < MIN_RECORDS:
        raise ValueError(
            f"{len(fresh)} requests: Bedrock needs at least {MIN_RECORDS} per job; "
            "run the script without --batch to finish them on demand"
        )
    lines = [json.dumps(record(r), ensure_ascii=False) for r in fresh]
    stamp = datetime.datetime.now(datetime.UTC).strftime("%Y%m%d-%H%M%S")
    started = []
    for number, chunk in enumerate(split_jobs(lines)):
        name = f"kb-{work.name}-{stamp}-{number}"[:63]
        prefix = f"{work.name}/{name}"
        s3.put_object(
            Bucket=bucket,
            Key=f"{prefix}/input.jsonl",
            Body=("\n".join(chunk) + "\n").encode("utf-8"),
        )
        response = bedrock.create_model_invocation_job(
            jobName=name,
            roleArn=role_arn,
            modelId=model_id,
            inputDataConfig={
                "s3InputDataConfig": {"s3Uri": f"s3://{bucket}/{prefix}/input.jsonl"}
            },
            outputDataConfig={
                "s3OutputDataConfig": {"s3Uri": f"s3://{bucket}/{prefix}/output/"}
            },
        )
        job = {
            "name": name,
            "arn": response["jobArn"],
            "bucket": bucket,
            "prefix": prefix,
            "records": len(chunk),
            "keys": [json.loads(line)["recordId"] for line in chunk],
            "status": "Submitted",
            "fetched": False,
        }
        jobs.append(job)
        started.append(job)
    write_json(jobs_file(work), jobs)
    return started


def refresh(work: Path, region: str, session: Any = None) -> list[dict]:
    """Update and return the status of every job."""
    import boto3

    session = session or boto3.Session()
    bedrock = session.client("bedrock", region_name=region)
    jobs = load_jobs(work)
    for job in jobs:
        if job["status"] in ("Completed", "Failed", "Stopped", "Expired"):
            continue
        detail = bedrock.get_model_invocation_job(jobIdentifier=job["arn"])
        job["status"] = detail["status"]
        job["message"] = detail.get("message", "")
    write_json(jobs_file(work), jobs)
    return jobs


def fetch(work: Path, region: str, session: Any = None) -> dict:
    """Download the answers of completed jobs into ``answers.jsonl``."""
    import boto3

    session = session or boto3.Session()
    s3 = session.client("s3", region_name=region)
    jobs = refresh(work, region, session)
    answers_file = work / "batch" / "answers.jsonl"
    report = {"jobs_fetched": 0, "answers": 0, "errors": 0, "usage": {}}
    for job in jobs:
        if job["fetched"] or job["status"] not in ("Completed", "PartiallyCompleted"):
            continue
        listing = s3.list_objects_v2(
            Bucket=job["bucket"], Prefix=f"{job['prefix']}/output/"
        )
        for item in listing.get("Contents", []):
            if not item["Key"].endswith(".jsonl.out"):
                continue
            body = s3.get_object(Bucket=job["bucket"], Key=item["Key"])["Body"].read()
            answers, errors = parse_output(body.decode("utf-8"))
            with answers_file.open("a", encoding="utf-8") as handle:
                for answer in answers:
                    handle.write(
                        json.dumps(
                            {"key": answer["key"], "text": answer["text"]},
                            ensure_ascii=False,
                        )
                        + "\n"
                    )
                    for name, value in answer["usage"].items():
                        if isinstance(value, int):
                            report["usage"][name] = report["usage"].get(name, 0) + value
            report["answers"] += len(answers)
            report["errors"] += len(errors)
        job["fetched"] = True
        report["jobs_fetched"] += 1
    write_json(jobs_file(work), jobs)
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("step", choices=["submit", "status", "fetch"])
    parser.add_argument("--work", type=Path, required=True)
    parser.add_argument("--bucket")
    parser.add_argument("--role-arn")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--region", default=DEFAULT_REGION)
    args = parser.parse_args(argv)
    if args.step == "submit":
        if not args.bucket or not args.role_arn:
            parser.error("submit needs --bucket and --role-arn")
        started = submit(args.work, args.bucket, args.role_arn, args.model, args.region)
        for job in started:
            print(f"{job['name']}: {job['records']} records")
        if not started:
            print("nothing to submit", file=sys.stderr)
        return 0
    if args.step == "status":
        for job in refresh(args.work, args.region):
            print(f"{job['name']}: {job['status']} ({job['records']} records)")
        return 0
    print(fetch(args.work, args.region))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
