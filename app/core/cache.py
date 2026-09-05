"""
Semantic cache: many Yelp review contents are highly similar (patterns like "food's good, service is
slow" recur over and over). An exact string-match cache alone would have a low hit rate; using embedding
similarity matching can significantly reduce the number of LLM calls.

In production, candidate vectors should be stored in Qdrant/pgvector for ANN retrieval; here numpy
brute-force cosine similarity is used to keep the skeleton simple and runnable without extra
infrastructure, with a comment noting "what this should be swapped for in production."
"""
from __future__ import annotations

import json
from dataclasses import dataclass

import numpy as np
from redis.asyncio import Redis


@dataclass
class CacheHit:
    reply_draft: str
    similarity: float


class SemanticCache:
    def __init__(self, redis: Redis, similarity_threshold: float):
        self._redis = redis
        self._threshold = similarity_threshold

    @staticmethod
    def _cosine(a: np.ndarray, b: np.ndarray) -> float:
        return float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b) + 1e-9))

    async def lookup(self, tenant_id: str, embedding: list[float]) -> CacheHit | None:
        index_key = f"semcache:index:{tenant_id}"
        entries = await self._redis.hgetall(index_key)
        if not entries:
            return None

        query_vec = np.array(embedding, dtype=np.float32)
        best: CacheHit | None = None
        for _, raw in entries.items():
            record = json.loads(raw)
            candidate_vec = np.array(record["embedding"], dtype=np.float32)
            score = self._cosine(query_vec, candidate_vec)
            if score >= self._threshold and (best is None or score > best.similarity):
                best = CacheHit(reply_draft=record["reply_draft"], similarity=score)
        return best

    async def store(self, tenant_id: str, review_id: str, embedding: list[float], reply_draft: str) -> None:
        index_key = f"semcache:index:{tenant_id}"
        payload = json.dumps({"embedding": embedding, "reply_draft": reply_draft})
        await self._redis.hset(index_key, review_id, payload)
        await self._redis.expire(index_key, 60 * 60 * 24 * 7)  # 7-day expiry, to avoid unbounded growth
