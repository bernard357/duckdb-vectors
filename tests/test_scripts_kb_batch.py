"""Bedrock batch jobs (scripts/kb_batch.py) and the batch queue they fill."""

import json

from development.curation import (
    AnswerCache,
    Model,
)
from development.kb_testing import fake_invoke
from scripts import (
    kb_context,
)


def test_batch_mode_queues_then_completes_from_fetched_answers(kb, tmp_path):
    from development.curation import BatchQueue, request_key
    from scripts import kb_batch

    queue = BatchQueue(tmp_path / "batch")
    model = Model("fake", batch=queue)
    cache = AnswerCache(tmp_path / "cache", model.usage)
    contexts = kb_context.contextualize_folder(kb, model, cache, workers=2)
    assert all(not page for page in contexts.values())  # nothing answered yet
    waiting = queue.save()
    assert waiting == queue.deferred > 0
    pending = [
        json.loads(line)
        for line in (tmp_path / "batch" / "pending.jsonl").read_text().splitlines()
    ]
    rows = []
    for request in pending:
        batch_record = kb_batch.record(request)
        assert batch_record["modelInput"]["messages"][0]["role"] == "user"
        text = fake_invoke(
            request["system"], [{"text": t} for t in request["texts"]], 300
        )
        rows.append(
            json.dumps(
                {
                    "recordId": request["key"],
                    "modelOutput": {
                        "content": [{"type": "text", "text": text}],
                        "usage": {"input_tokens": 10, "output_tokens": 2},
                    },
                }
            )
        )
    rows.append(json.dumps({"recordId": "x", "error": {"errorMessage": "boom"}}))
    answers, errors = kb_batch.parse_output("\n".join(rows))
    assert len(answers) == waiting and len(errors) == 1
    with (tmp_path / "batch" / "answers.jsonl").open("w") as handle:
        for answer in answers:
            handle.write(json.dumps({"key": answer["key"], "text": answer["text"]}))
            handle.write("\n")
    replay = Model("fake", batch=BatchQueue(tmp_path / "batch"))
    done = kb_context.contextualize_folder(kb, replay, cache, workers=2)
    assert replay.batch.deferred == 0
    assert all(page for page in done.values())
    assert request_key("m", "s", [{"text": "a"}, {"cachePoint": {}}], 5) == (
        request_key("m", "s", [{"text": "a"}], 5)
    )


def test_batch_jobs_respect_the_record_limit(monkeypatch):
    from scripts import kb_batch

    monkeypatch.setattr(kb_batch, "MAX_RECORDS", 3)
    assert [len(job) for job in kb_batch.split_jobs(["{}"] * 7)] == [3, 3, 1]
