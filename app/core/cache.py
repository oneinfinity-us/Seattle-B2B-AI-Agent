"""
语义缓存：许多 Yelp 评论内容高度相似（"味道不错，服务慢"这种模式反复出现）。
如果只做精确字符串匹配缓存，命中率很低；用 embedding 相似度匹配可以显著降低 LLM 调用次数。

生产环境建议把候选向量放 Qdrant/pgvector 做 ANN 检索；
这里用 numpy 暴力计算余弦相似度，是为了骨架简单、无需额外基础设施即可跑通，
并在注释里标出"生产环境该换成什么"。
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
        await self._redis.expire(index_key, 60 * 60 * 24 * 7)  # 7 天过期，避免无限增长
