"""Local vector-derived index for durable SQLite Memory Records."""

from __future__ import annotations

from math import isfinite, sqrt
import json
import os
import time
from typing import Any, Protocol

import httpx

from .sqlite_database import SQLiteDatabase
from .sqlite_stores import SQLiteMemoryStore
from antisentinel.memory.hybrid_ranker import RetrievalCandidate
from antisentinel.memory.models import AuthorizedMemoryScope


class Embedder(Protocol):
    model_name: str
    dimension: int
    def embed(self, texts: list[str]) -> list[list[float]]: ...


class LocalVectorMemory:
    def __init__(self, database: SQLiteDatabase, embedder: Embedder) -> None:
        self.database = database
        self.embedder = embedder
        self.channel_status: dict[str, str] = {"vector": "active"}

    def upsert(self, record: dict[str, Any]) -> None:
        memory_id = record.get("memory_id")
        if not memory_id:
            raise ValueError("memory_id is required")
        vector = record.get("embedding")
        if vector is None:
            vector = self._embed_documents([str(record.get("content", ""))])[0]
        self._validate(vector)
        durable_record = {key: value for key, value in record.items() if key != "embedding"}
        SQLiteMemoryStore(self.database).replace(durable_record)
        with self.database.transaction() as connection:
            connection.execute(
                """
                INSERT INTO memory_vectors(memory_id,embedding_model,dimension,vector_json,content_version,updated_at)
                VALUES(?,?,?,?,?,CURRENT_TIMESTAMP)
                ON CONFLICT(memory_id) DO UPDATE SET
                    embedding_model=excluded.embedding_model,dimension=excluded.dimension,
                    vector_json=excluded.vector_json,content_version=excluded.content_version,updated_at=excluded.updated_at
                """,
                (memory_id, self.embedder.model_name, self.embedder.dimension, json.dumps(vector, separators=(",", ":")), int(record.get("content_version", 1))),
            )

    def search(self, query: str, *, operator_id: str | None = None, incident_id: str | None = None, limit: int = 10) -> list[dict[str, Any]]:
        if limit < 1:
            raise ValueError("limit must be positive")
        query_vector = self._embed_query([query])[0]
        self._validate(query_vector)
        where = ["m.status = 'active'", "v.embedding_model = ?", "v.dimension = ?", "v.content_version = m.content_version"]
        params: list[Any] = [self.embedder.model_name, self.embedder.dimension]
        if operator_id is not None:
            where.append("(m.operator_id = ? OR m.operator_id IS NULL)"); params.append(operator_id)
        if incident_id is not None:
            where.append("(m.incident_id = ? OR m.incident_id IS NULL)"); params.append(incident_id)
        rows = self.database.query(
            f"SELECT m.record_json,v.vector_json FROM memory_vectors v JOIN memory_records m ON m.memory_id=v.memory_id WHERE {' AND '.join(where)}",
            params,
        )
        scored = []
        for row in rows:
            record = json.loads(row["record_json"]); score = _cosine(query_vector, json.loads(row["vector_json"]))
            scored.append((score, record))
        scored.sort(key=lambda item: (-item[0], item[1]["memory_id"]))
        return [{**record, "vector_score": score} for score, record in scored[:limit]]

    def vector_candidates(self, query: str, scope: AuthorizedMemoryScope, *, limit: int) -> tuple[RetrievalCandidate, ...]:
        try:
            rows = self.search(query, operator_id=scope.operator_id, incident_id=scope.incident_id, limit=limit)
        except RuntimeError as exc:
            if str(exc) not in {"embedding_transport_error"} and not str(exc).startswith("embedding_http_"):
                raise
            self.channel_status["vector"] = "bypass"
            return ()
        self.channel_status["vector"] = "active"
        return tuple(RetrievalCandidate(str(row["memory_id"]), "vector", rank, float(row["vector_score"])) for rank, row in enumerate(rows, 1))

    def _validate(self, vector: list[float]) -> None:
        if len(vector) != self.embedder.dimension:
            raise ValueError(f"embedding dimension {len(vector)} != {self.embedder.dimension}")
        if any(not isinstance(value, (int, float)) or isinstance(value, bool) for value in vector):
            raise ValueError("embedding values must be numeric")

    def _embed_documents(self, texts: list[str]) -> list[list[float]]:
        method = getattr(self.embedder, "embed_documents", self.embedder.embed)
        return method(texts)

    def _embed_query(self, texts: list[str]) -> list[list[float]]:
        method = getattr(self.embedder, "embed_query", self.embedder.embed)
        return method(texts)


def _cosine(left: list[float], right: list[float]) -> float:
    left_norm = sqrt(sum(value * value for value in left)); right_norm = sqrt(sum(value * value for value in right))
    if not left_norm or not right_norm:
        return 0.0
    return sum(a * b for a, b in zip(left, right, strict=True)) / (left_norm * right_norm)


class QwenFlashEmbedder:
    """Bailian OpenAI-compatible embeddings; vector storage remains local.

    Query/document methods deliberately send plain text: E5's query/passage
    prefixes are model-specific and must not be carried across this migration.
    """

    model_name = "qwen3.7-text-embedding-flash"

    def __init__(
        self, *, base_url: str, api_key: str, dimension: int = 1024,
        batch_size: int = 20, timeout: float = 30.0, max_retries: int = 2,
        client: httpx.Client | None = None,
    ) -> None:
        if not api_key.strip():
            raise ValueError("DASHSCOPE_API_KEY is required")
        url = httpx.URL(base_url.strip())
        if url.scheme != "https" or not url.host or url.userinfo or url.query or url.fragment:
            raise ValueError("embedding base_url must be an HTTPS URL without credentials or query")
        if type(dimension) is not int or dimension not in {256, 512, 768, 1024}:
            raise ValueError("Flash dimension must be 256, 512, 768 or 1024")
        if type(batch_size) is not int or not 1 <= batch_size <= 20:
            raise ValueError("batch_size must be between 1 and 20")
        if not isfinite(timeout) or timeout <= 0:
            raise ValueError("timeout must be positive and finite")
        if type(max_retries) is not int or not 0 <= max_retries <= 2:
            raise ValueError("max_retries must be between 0 and 2")
        base = str(url).rstrip("/")
        self.endpoint = base if base.endswith("/embeddings") else f"{base}/embeddings"
        self.dimension = dimension
        self.batch_size = batch_size
        self.timeout = timeout
        self.max_retries = max_retries
        self._api_key = api_key
        self._owns_client = client is None
        self.client = client if client is not None else httpx.Client(timeout=timeout, follow_redirects=False)

    @classmethod
    def from_env(cls) -> "QwenFlashEmbedder":
        api_key = os.getenv("DASHSCOPE_API_KEY", "").strip()
        if not api_key:
            raise ValueError("DASHSCOPE_API_KEY is required")
        base_url = os.getenv("ANTISENTINEL_EMBEDDING_BASE_URL", "").strip()
        if not base_url:
            raise ValueError("ANTISENTINEL_EMBEDDING_BASE_URL is required")
        dimension = int(os.getenv("ANTISENTINEL_EMBEDDING_DIMENSION", "1024"))
        return cls(base_url=base_url, api_key=api_key, dimension=dimension)

    def close(self) -> None:
        if self._owns_client:
            self.client.close()

    def __enter__(self) -> "QwenFlashEmbedder":
        return self

    def __exit__(self, *_: Any) -> None:
        self.close()

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return self.embed(texts)

    def embed_query(self, texts: list[str]) -> list[list[float]]:
        return self.embed(texts)

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not isinstance(texts, list) or any(not isinstance(text, str) or not text.strip() for text in texts):
            raise ValueError("embedding input must be a list of non-empty strings")
        vectors: list[list[float]] = []
        for start in range(0, len(texts), self.batch_size):
            batch = texts[start:start + self.batch_size]
            response = self._request(batch)
            vectors.extend(self._parse(response, len(batch)))
        return vectors

    def _request(self, batch: list[str]) -> httpx.Response:
        for attempt in range(self.max_retries + 1):
            try:
                response = self.client.post(
                    self.endpoint,
                    headers={"Authorization": f"Bearer {self._api_key}"},
                    json={"model": self.model_name, "input": batch, "dimensions": self.dimension, "encoding_format": "float"},
                    timeout=self.timeout, follow_redirects=False,
                )
            except httpx.TransportError:
                if attempt == self.max_retries:
                    raise RuntimeError("embedding_transport_error") from None
            else:
                if response.status_code == 200:
                    return response
                if response.status_code not in {408, 429, 500, 502, 503, 504} or attempt == self.max_retries:
                    raise RuntimeError(f"embedding_http_{response.status_code}")
            time.sleep(0.5 * (2 ** attempt))
        raise RuntimeError("embedding_retries_exhausted")

    def _parse(self, response: httpx.Response, count: int) -> list[list[float]]:
        try:
            body = response.json()
            if body["model"] != self.model_name or len(body["data"]) != count:
                raise ValueError
            result: dict[int, list[float]] = {}
            for item in body["data"]:
                index, vector = item["index"], item["embedding"]
                if type(index) is not int or index not in range(count) or index in result:
                    raise ValueError
                if not isinstance(vector, list) or len(vector) != self.dimension:
                    raise ValueError
                if any(type(value) not in (int, float) or not isfinite(value) for value in vector):
                    raise ValueError
                if not any(vector):
                    raise ValueError
                result[index] = [float(value) for value in vector]
            return [result[index] for index in range(count)]
        except (ValueError, KeyError, TypeError, OverflowError):
            raise ValueError("invalid_embedding_response") from None
