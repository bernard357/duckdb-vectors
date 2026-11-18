---
prompt: query-contextual
version: 1
purpose: Write a question that only one of two similar passages answers, as a user who knows what they are looking for would ask it (the context-dependent stratum).
stage: validation set, context-dependent stratum
inputs: [page, passage, confuser_page, confuser]
output: JSON object, see the user prompt
sees_queries: false
sees_tags: false
---
# Context-dependent query prompt, version 1

## System

You write search questions for evaluating a retrieval system on a knowledge
base. Many passages of a knowledge base look alike once they are cut from their
pages: two procedures, two contracts or two reports can share most of their
words and differ only in what they are about. You write the questions that tell
such passages apart, the way a person who knows what they need would ask them.

## User

Passage A comes from the page whose opening is shown below. Passage B comes
from another page and looks similar.

Write one question that passage A answers and passage B does not, with a short
reference answer taken from passage A.

Rules:

- Ask what a real person would type into a search box. They know which system,
  product, party, period or procedure they care about, and they may name it, as
  long as the name is how a person would refer to it (as the page opening does),
  not a file name or a section number.
- The question must be answerable from passage A, read with its page.
- Do not copy more than four consecutive words from passage A.
- Do not write "passage", "page", "document", "section" or "text".
- If no such question exists, because the two passages say the same thing about
  the same subject, answer with `null` for the question.

Answer with one JSON object and nothing else:

```json
{"question": "", "answer": ""}
```

Opening of the page of passage A:

{page}

Passage A:

{passage}

Opening of the page of passage B:

{confuser_page}

Passage B:

{confuser}
