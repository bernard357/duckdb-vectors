---
prompt: contextual-header
version: 1
purpose: Write the chunk-specific context of Anthropic's Contextual Retrieval, for the reference run.
stage: reference condition (hypothesis H4), not part of the deterministic method
inputs: [document, chunk]
output: plain text, the context only
sees_queries: false
sees_tags: false
source: the prompt published in Anthropic's Contextual Retrieval post, used word for word apart from the placeholders
---
# Contextual header prompt, version 1

## System

You write short contexts that situate a chunk within its document.

## User

<document>
{document}
</document>

Here is the chunk we want to situate within the whole document

<chunk>
{chunk}
</chunk>

Please give a short succinct context to situate this chunk within the overall
document for the purposes of improving search retrieval of the chunk. Answer
only with the succinct context and nothing else.
