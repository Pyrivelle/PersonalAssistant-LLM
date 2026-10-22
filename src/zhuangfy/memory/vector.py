"""Replaceable embedding and Qdrant adapters for ZhuangFY's first memory index.

The authoritative text lives in SQLite; Qdrant stores only UUIDs, scope
identifiers and vectors. This module intentionally does not import LLM/Qt/QQ.
"""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, Sequence


def checked_vector(values: Sequence[float], dimension: int) -> list[float]:
    """Validate model output before allowing it into a vector collection."""
    if dimension <= 0 or len(values) != dimension:
        raise ValueError(f"Expected a {dimension}-dimensional embedding, got {len(values)}")
    vector = [float(v) for v in values]
    if not all(math.isfinite(v) for v in vector):
        raise ValueError("Embeddings must contain only finite numbers")
    if not any(v != 0 for v in vector):
        raise ValueError("A zero embedding cannot be used for cosine search")
    return vector


class EmbeddingProvider(Protocol):
    model_id: str
    dimension: int

    def embed_document(self, text: str) -> Sequence[float]: ...

    def embed_query(self, text: str) -> Sequence[float]: ...


class FastEmbedProvider:
    """Optional local Chinese text embedding; weights download on first use.

    fastembed is intentionally optional: importing the memory package cannot
    download a model or require ONNX on GitHub CI.
    """

    def __init__(
        self,
        model_id: str = "BAAI/bge-small-zh-v1.5",
        dimension: int = 512,
        cache_dir: Path | None = None,
    ) -> None:
        if not model_id.strip() or dimension <= 0:
            raise ValueError("Embedding model and dimension must be specified")
        self.model_id = model_id
        self.dimension = dimension
        self.cache_dir = cache_dir
        self._model = None

    def _load(self):
        if self._model is None:
            try:
                from fastembed import TextEmbedding
            except ImportError as exc:
                raise RuntimeError("Install the optional dependency: pip install -e '.[memory-vector]'") from exc
            self._model = TextEmbedding(
                model_name=self.model_id,
                **({"cache_dir": str(self.cache_dir)} if self.cache_dir else {}),
            )
        return self._model

    def embed_document(self, text: str) -> list[float]:
        if not text.strip():
            raise ValueError("Cannot embed empty text")
        return checked_vector(next(iter(self._load().embed([text]))), self.dimension)

    def embed_query(self, text: str) -> list[float]:
        if not text.strip():
            raise ValueError("Cannot embed an empty query")
        return checked_vector(next(iter(self._load().query_embed([text]))), self.dimension)


@dataclass(frozen=True)
class VectorHit:
    memory_id: str
    score: float


class VectorIndex(Protocol):
    """Scoped vector index; implement with Qdrant now, other stores later."""

    def upsert(self, memory_id: str, agent_id: str, person_id: str, vector: Sequence[float]) -> None: ...

    def search(self, agent_id: str, person_id: str, vector: Sequence[float], limit: int) -> list[VectorHit]: ...

    def delete(self, memory_id: str) -> None: ...

    def close(self) -> None: ...


class QdrantLocalIndex:
    """Local Qdrant; one process must own the storage directory at a time.

    Collections are namespaced by embedding model and dimension so switching
    providers never silently mixes incompatible vector spaces.
    """

    def __init__(
        self,
        directory: Path,
        *,
        model_id: str,
        dimension: int,
        client=None,
    ) -> None:
        if not model_id.strip() or dimension <= 0:
            raise ValueError("model_id and positive dimension are required")
        try:
            from qdrant_client import QdrantClient, models
        except ImportError as exc:
            raise RuntimeError("Install the optional dependency: pip install -e '.[memory-vector]'") from exc
        self.dimension = dimension
        self.model_id = model_id
        self._models = models
        self._owns_client = client is None
        if client is None:
            Path(directory).mkdir(parents=True, exist_ok=True)
        self._client = client or QdrantClient(path=str(directory))
        digest = hashlib.sha256(model_id.encode("utf-8")).hexdigest()[:12]
        self.collection = f"zhuangfy_mem_v1_{dimension}_{digest}"
        try:
            if not self._client.collection_exists(self.collection):
                self._client.create_collection(
                    collection_name=self.collection,
                    vectors_config=models.VectorParams(size=dimension, distance=models.Distance.COSINE),
                )
            else:
                info = self._client.get_collection(self.collection)
                config = info.config.params.vectors
                actual = config.size if hasattr(config, "size") else None
                if actual != dimension:
                    raise ValueError(f"Incompatible existing Qdrant collection: {actual} != {dimension}")
        except Exception:
            if self._owns_client:
                self._client.close()
            raise

    def upsert(self, memory_id: str, agent_id: str, person_id: str, vector: Sequence[float]) -> None:
        self._client.upsert(
            collection_name=self.collection,
            wait=True,
            points=[self._models.PointStruct(
                id=memory_id,
                vector=checked_vector(vector, self.dimension),
                payload={"agent_id": agent_id, "person_id": person_id},
            )],
        )

    def search(self, agent_id: str, person_id: str, vector: Sequence[float], limit: int = 5) -> list[VectorHit]:
        if not agent_id or not person_id:
            raise ValueError("An agent and an authenticated person are required")
        if not 1 <= limit <= 50:
            raise ValueError("limit must be between 1 and 50")
        response = self._client.query_points(
            collection_name=self.collection,
            query=checked_vector(vector, self.dimension),
            query_filter=self._models.Filter(must=[
                self._models.FieldCondition(key="agent_id", match=self._models.MatchValue(value=agent_id)),
                self._models.FieldCondition(key="person_id", match=self._models.MatchValue(value=person_id)),
            ]),
            with_payload=False,
            limit=limit,
        )
        return [VectorHit(str(item.id), float(item.score)) for item in response.points]

    def delete(self, memory_id: str) -> None:
        self._client.delete(
            collection_name=self.collection,
            points_selector=self._models.PointIdsList(points=[memory_id]),
            wait=True,
        )

    def close(self) -> None:
        if self._owns_client:
            self._client.close()
