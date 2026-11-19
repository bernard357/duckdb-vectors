"""Helpers shared by the tests, the benchmarks and the scripts of the repository.

This package sits next to ``duckdb_vectors`` in ``src/`` so that the editable
install makes it importable everywhere, but it is not part of the published
wheel: nothing in ``duckdb_vectors`` may import it.

* :mod:`development.paths`: where fixtures, prompts and benchmark data live.
* :mod:`development.metrics`: retrieval metrics and paired statistical tests.
* :mod:`development.datasets`: loaders of the public benchmark datasets.
* :mod:`development.curation`: what the knowledge-base curation scripts share:
  versioned prompts, the Bedrock model and its batch queue, the answer cache,
  the front matter editor, and folder chunking and indexing.
* :mod:`development.fake_model`: a deterministic stand-in for the model.
* :mod:`development.ping`: constants and checks of the end-to-end ping scripts.
"""
