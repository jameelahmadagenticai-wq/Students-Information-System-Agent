import time
import math
import re
from typing import Optional
from collections import OrderedDict
from app.core.logging import logger


class SemanticQueryCache:
    """
    High-performance in-memory semantic and exact query cache.
    Stores user queries, final LLM answers, and citations with TTL and LRU eviction.
    Supports auto-invalidation on document upload.
    """

    def __init__(self, max_size: int = 1000, ttl_seconds: int = 7200):
        self.max_size = max_size
        self.ttl_seconds = ttl_seconds
        # OrderedDict for LRU behavior: key -> entry dict
        self._cache: OrderedDict[str, dict] = OrderedDict()

    @staticmethod
    def _normalize(text: str) -> str:
        text = text.lower().strip()
        text = re.sub(r"[^\w\s]", "", text)
        return " ".join(text.split())

    STOPWORDS = {
        "what", "is", "the", "a", "an", "and", "or", "in", "on", "at", "to",
        "for", "of", "with", "tell", "me", "about", "can", "you", "how", "do",
        "i", "does", "please", "give", "show", "are"
    }

    @classmethod
    def _get_token_vector(cls, text: str) -> dict[str, float]:
        tokens = [t for t in text.split() if t not in cls.STOPWORDS]
        if not tokens:
            tokens = text.split()
        if not tokens:
            return {}
        counts: dict[str, float] = {}
        for t in tokens:
            counts[t] = counts.get(t, 0.0) + 1.0
        # Normalize vector to unit length
        norm = math.sqrt(sum(v * v for v in counts.values()))
        if norm > 0:
            for k in counts:
                counts[k] /= norm
        return counts

    @staticmethod
    def _cosine_similarity(vec1: dict[str, float], vec2: dict[str, float]) -> float:
        if not vec1 or not vec2:
            return 0.0
        # Dot product of unit vectors
        dot = sum(val * vec2.get(word, 0.0) for word, val in vec1.items())
        return dot

    def get(self, tenant_id: str, role: str, query: str, similarity_threshold: float = 0.78) -> Optional[dict]:
        clean_query = self._normalize(query)
        if not clean_query:
            return None

        now = time.time()
        exact_key = f"{tenant_id}:{role}:{clean_query}"

        # 1. Exact Cache Hit (O(1))
        if exact_key in self._cache:
            entry = self._cache[exact_key]
            if now - entry["timestamp"] <= self.ttl_seconds:
                # Move to end for LRU
                self._cache.move_to_end(exact_key)
                logger.info("Exact Cache Hit", query=query, tenant_id=tenant_id)
                res = dict(entry["response"])
                res["cached"] = True
                res["cache_type"] = "exact"
                return res
            else:
                del self._cache[exact_key]

        # 2. Semantic Cache Hit (Cosine similarity over token vectors)
        query_vec = self._get_token_vector(clean_query)
        best_match = None
        best_score = 0.0
        best_key = None

        for k, entry in list(self._cache.items()):
            if entry["tenant_id"] != tenant_id or entry["role"] != role:
                continue

            # Check expiration
            if now - entry["timestamp"] > self.ttl_seconds:
                continue

            score = self._cosine_similarity(query_vec, entry["token_vector"])
            if score > best_score:
                best_score = score
                best_match = entry
                best_key = k

        if best_match and best_score >= similarity_threshold:
            self._cache.move_to_end(best_key)
            logger.info(
                "Semantic Cache Hit",
                query=query,
                matched_query=best_match["raw_query"],
                similarity=round(best_score, 3),
            )
            res = dict(best_match["response"])
            res["cached"] = True
            res["cache_type"] = "semantic"
            res["cache_similarity"] = round(best_score, 3)
            return res

        return None

    def set(
        self,
        tenant_id: str,
        role: str,
        query: str,
        answer: str,
        citations: list[dict],
        sources_used: int,
    ) -> None:
        clean_query = self._normalize(query)
        if not clean_query:
            return

        exact_key = f"{tenant_id}:{role}:{clean_query}"
        now = time.time()

        # Evict oldest if full
        if len(self._cache) >= self.max_size:
            oldest_key, _ = self._cache.popitem(last=False)
            logger.debug("Cache LRU evicted oldest key", evicted=oldest_key)

        entry = {
            "tenant_id": tenant_id,
            "role": role,
            "raw_query": query,
            "token_vector": self._get_token_vector(clean_query),
            "timestamp": now,
            "response": {
                "answer": answer,
                "citations": citations,
                "sources_used": sources_used,
            },
        }

        self._cache[exact_key] = entry
        logger.info("Saved query response to cache", key=exact_key, total_cached=len(self._cache))

    def invalidate_tenant(self, tenant_id: str) -> int:
        """Flushes all cached answers for a given tenant when a document is uploaded."""
        keys_to_delete = [
            k for k, v in self._cache.items() if v.get("tenant_id") == tenant_id
        ]
        for k in keys_to_delete:
            del self._cache[k]
        logger.info("Tenant cache invalidated after document upload", tenant_id=tenant_id, flushed_count=len(keys_to_delete))
        return len(keys_to_delete)

    def stats(self) -> dict:
        return {
            "total_entries": len(self._cache),
            "max_size": self.max_size,
            "ttl_seconds": self.ttl_seconds,
        }


# Global singleton instance for RAG
rag_cache = SemanticQueryCache(max_size=1000, ttl_seconds=7200)


class SemanticSQLCache:
    """
    High-performance in-memory semantic and exact cache for Natural Language to SQL queries.
    Caches parsed SQL queries, executed database results, and summaries to save 2 LLM calls per hit.
    Default TTL: 30 minutes (1800 seconds).
    """

    def __init__(self, max_size: int = 1000, ttl_seconds: int = 1800):
        self.max_size = max_size
        self.ttl_seconds = ttl_seconds
        self._cache: OrderedDict[str, dict] = OrderedDict()

    @staticmethod
    def _normalize(text: str) -> str:
        text = text.lower().strip()
        text = re.sub(r"[^\w\s]", "", text)
        return " ".join(text.split())

    STOPWORDS = {
        "what", "is", "the", "a", "an", "and", "or", "in", "on", "at", "to",
        "for", "of", "with", "tell", "me", "about", "can", "you", "how", "do",
        "i", "does", "please", "give", "show", "are", "many", "all", "our"
    }

    @classmethod
    def _get_token_vector(cls, text: str) -> dict[str, float]:
        tokens = [t for t in text.split() if t not in cls.STOPWORDS]
        if not tokens:
            tokens = text.split()
        if not tokens:
            return {}
        counts: dict[str, float] = {}
        for t in tokens:
            counts[t] = counts.get(t, 0.0) + 1.0
        norm = math.sqrt(sum(v * v for v in counts.values()))
        if norm > 0:
            for k in counts:
                counts[k] /= norm
        return counts

    @staticmethod
    def _cosine_similarity(vec1: dict[str, float], vec2: dict[str, float]) -> float:
        if not vec1 or not vec2:
            return 0.0
        return sum(val * vec2.get(word, 0.0) for word, val in vec1.items())

    def get(self, tenant_id: str, role: str, query: str, similarity_threshold: float = 0.85) -> Optional[dict]:
        clean_query = self._normalize(query)
        if not clean_query:
            return None

        now = time.time()
        exact_key = f"{tenant_id}:{role}:{clean_query}"

        # 1. Exact Hit (O(1))
        if exact_key in self._cache:
            entry = self._cache[exact_key]
            if now - entry["timestamp"] <= self.ttl_seconds:
                self._cache.move_to_end(exact_key)
                logger.info("Exact SQL Cache Hit", query=query, role=role, tenant_id=tenant_id)
                res = dict(entry["response"])
                res["cached"] = True
                res["cache_type"] = "exact"
                return res
            else:
                del self._cache[exact_key]

        # 2. Semantic Hit (Cosine Similarity)
        query_vec = self._get_token_vector(clean_query)
        best_match = None
        best_score = 0.0
        best_key = None

        for k, entry in list(self._cache.items()):
            if entry["tenant_id"] != tenant_id or entry["role"] != role:
                continue

            if now - entry["timestamp"] > self.ttl_seconds:
                continue

            score = self._cosine_similarity(query_vec, entry["token_vector"])
            if score > best_score:
                best_score = score
                best_match = entry
                best_key = k

        if best_match and best_score >= similarity_threshold:
            self._cache.move_to_end(best_key)
            logger.info(
                "Semantic SQL Cache Hit",
                query=query,
                matched_query=best_match["raw_query"],
                similarity=round(best_score, 3),
            )
            res = dict(best_match["response"])
            res["cached"] = True
            res["cache_type"] = "semantic"
            res["cache_similarity"] = round(best_score, 3)
            return res

        return None

    def set(self, tenant_id: str, role: str, query: str, response_payload: dict) -> None:
        clean_query = self._normalize(query)
        if not clean_query:
            return

        exact_key = f"{tenant_id}:{role}:{clean_query}"
        now = time.time()

        if len(self._cache) >= self.max_size:
            self._cache.popitem(last=False)

        # Store response copy
        entry = {
            "tenant_id": tenant_id,
            "role": role,
            "raw_query": query,
            "token_vector": self._get_token_vector(clean_query),
            "timestamp": now,
            "response": dict(response_payload),
        }
        self._cache[exact_key] = entry
        logger.info("Saved NL-to-SQL response to cache", key=exact_key, total_cached=len(self._cache))

    def invalidate_tenant(self, tenant_id: str) -> int:
        keys_to_delete = [k for k, v in self._cache.items() if v.get("tenant_id") == tenant_id]
        for k in keys_to_delete:
            del self._cache[k]
        return len(keys_to_delete)


# Global singleton instance for SQL Agent
sql_cache = SemanticSQLCache(max_size=1000, ttl_seconds=1800)
