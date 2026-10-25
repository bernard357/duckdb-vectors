"""Amazon Bedrock embedding backend (Titan Embed).

Installed via the ``bedrock`` extra (``pip install duckdb-vectors[bedrock]``).
Embedding is one HTTP round-trip per text, so batches are embedded with a
bounded thread pool to cut wall-clock time. A boto3 ``session`` can be injected
for credential and region control; otherwise the default session is used.
"""

import json
import os
from concurrent.futures import ThreadPoolExecutor

_MODEL_IDS = {
    "titan": "amazon.titan-embed-text-v2:0",
    "titan-v1": "amazon.titan-embed-text-v1",
    "titan-v2": "amazon.titan-embed-text-v2:0",
}
_MODEL_DIMENSIONS = {
    "amazon.titan-embed-text-v1": 1536,
    "amazon.titan-embed-text-v2:0": 1024,
}


class BedrockEmbedder:
    """Embed text with a Bedrock Titan Embed model.

    ``model`` is a short alias (``titan``) or a full Bedrock model id. Pass a
    boto3 ``session`` to control credentials and region; ``max_workers`` bounds
    the embedding concurrency.
    """

    def __init__(
        self,
        model: str = "titan",
        session=None,
        region_name: str | None = None,
        max_workers: int = 16,
    ) -> None:
        self._model_id = _MODEL_IDS.get(model, model)
        self._session = session
        # Fall back to the ambient region (AWS_REGION is always set inside
        # Lambda; AWS_DEFAULT_REGION is the botocore convention elsewhere).
        self._region_name = (
            region_name
            or os.environ.get("AWS_REGION")
            or os.environ.get("AWS_DEFAULT_REGION")
        )
        self._max_workers = max_workers
        self._client = None
        self._dimensions = _MODEL_DIMENSIONS.get(self._model_id)

    def _runtime_client(self):
        if self._client is None:
            if self._session is not None:
                self._client = self._session.client(
                    "bedrock-runtime", region_name=self._region_name
                )
            else:
                try:
                    import boto3
                except ImportError as exc:  # pragma: no cover - import guard
                    raise ImportError(
                        "The 'bedrock' backend requires boto3. Install it with "
                        "'pip install duckdb-vectors[bedrock]'."
                    ) from exc
                self._client = boto3.client(
                    "bedrock-runtime", region_name=self._region_name
                )
        return self._client

    @property
    def model_identifier(self) -> str:
        """Identifier folded into the etag."""
        return f"bedrock:{self._model_id}"

    @property
    def dimensions(self) -> int:
        """Vector dimensionality, probed once if the model is unknown."""
        if self._dimensions is None:
            probe = self.embed(["dimension probe"])
            self._dimensions = len(probe[0])
        return self._dimensions

    def _embed_one(self, text: str, client) -> list[float]:
        response = client.invoke_model(
            modelId=self._model_id,
            body=json.dumps({"inputText": text}),
        )
        payload = json.loads(response["body"].read())
        return payload["embedding"]

    def embed(self, texts: list[str]) -> list[list[float]]:
        """Embed a batch concurrently, preserving order."""
        if not texts:
            return []
        client = self._runtime_client()
        if self._max_workers <= 1 or len(texts) == 1:
            vectors = [self._embed_one(text, client) for text in texts]
        else:
            with ThreadPoolExecutor(max_workers=self._max_workers) as pool:
                vectors = list(
                    pool.map(lambda text: self._embed_one(text, client), texts)
                )
        if self._dimensions is None and vectors:
            self._dimensions = len(vectors[0])
        return vectors
