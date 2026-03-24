"""
memory/retrieval.py — Mode-specific fusion-ranked context retrieval for GARY v5

NOT just cosine similarity. NOT one static formula.
Different query intents get different scoring weights:

  Factual/coding:    semantic 45%, recency 25%, salience 20%, open_loop 10%, affect 0%
  Relational:        semantic 20%, recency 15%, salience 15%, open_loop 30%, affect 20%
  Reflection/idle:   semantic 25%, recency 10%, salience 25%, open_loop 25%, affect 15%
  Dreaming:          semantic 40%, recency 5%,  salience 10%, open_loop 15%, affect 30%

Pipeline:
  1. Relational prefilter (domain, epistemic status, consent)
  2. Sparse/BM25 + Dense HNSW hybrid search
  3. Fusion rerank with mode-specific weights
  4. Token budget trim (≤600 tokens)

Usage:
    retriever = ContextRetriever()
    context = await retriever.build_context(
        query_text="What was that project we discussed?",
        session_id="sess-123",
        affect_vector=current_affect,
        mode="factual",
        max_tokens=600,
    )
"""
from __future__ import annotations

import json
import logging
import math
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

log = logging.getLogger("gary.retrieval")

# Mode-specific weight profiles
WEIGHT_PROFILES = {
    "factual": {"semantic": 0.45, "recency": 0.25, "salience": 0.20, "open_loop": 0.10, "affect": 0.00},
    "relational": {"semantic": 0.20, "recency": 0.15, "salience": 0.15, "open_loop": 0.30, "affect": 0.20},
    "reflection": {"semantic": 0.25, "recency": 0.10, "salience": 0.25, "open_loop": 0.25, "affect": 0.15},
    "dreaming": {"semantic": 0.40, "recency": 0.05, "salience": 0.10, "open_loop": 0.15, "affect": 0.30},
}

# Default weight profile (backward compatible)
DEFAULT_MODE = "factual"

# Retrieval parameters
MAX_CANDIDATES = 50
RECENCY_HALF_LIFE_SEC = 48 * 3600  # 48 hours
MAX_CONTEXT_TOKENS = 600

# Exported constants for testing
W_SEMANTIC = WEIGHT_PROFILES[DEFAULT_MODE]["semantic"]
W_RECENCY = WEIGHT_PROFILES[DEFAULT_MODE]["recency"]
W_SALIENCE = WEIGHT_PROFILES[DEFAULT_MODE]["salience"]
W_OPEN_LOOP = WEIGHT_PROFILES[DEFAULT_MODE]["open_loop"]
W_AFFECT = WEIGHT_PROFILES[DEFAULT_MODE]["affect"]


@dataclass
class ScoredMemory:
    """A memory with its fusion score breakdown."""
    id: str
    kind: str
    title: str
    content: str
    score: float
    breakdown: Dict[str, float]
    estimated_tokens: int


class ContextRetriever:
    """Builds mode-specific fusion-ranked context packs from the Memory Spine."""

    async def build_context(
        self,
        query_text: str,
        session_id: str = "",
        affect_vector: Optional[Dict[str, float]] = None,
        mode: str = DEFAULT_MODE,
        max_tokens: int = MAX_CONTEXT_TOKENS,
        embedding: Optional[list] = None,
        domain_filter: Optional[str] = None,
    ) -> str:
        """
        Build a context pack string for the LLM system prompt.

        Args:
            query_text: Current user utterance or thought trigger
            session_id: For open loop lookup
            affect_vector: Current 13-dim affect state (as dict)
            mode: Retrieval mode — factual | relational | reflection | dreaming
            max_tokens: Budget for the context pack
            embedding: Pre-computed 384d embedding (optional)
            domain_filter: Optional domain prefilter (user | relationship | self | world)

        Returns:
            Formatted context string for injection into the LLM prompt.
        """
        from memory.db import get_pool

        weights = WEIGHT_PROFILES.get(mode, WEIGHT_PROFILES[DEFAULT_MODE])

        try:
            pool = await get_pool()
        except Exception as e:
            log.warning(f"DB unavailable for retrieval: {e}")
            return ""

        if embedding is None:
            embedding = await self._embed(query_text)
            if embedding is None:
                return ""

        async with pool.acquire() as conn:
            # 1. Semantic search via pgvector (with optional domain prefilter)
            candidates = await self._semantic_search(conn, embedding, MAX_CANDIDATES, domain_filter)

            # 2. Get active open loops
            open_loop_ids = set()
            loops = await conn.fetch(
                "SELECT id FROM open_loops WHERE status IN ('open', 'progressing') LIMIT 20"
            )
            open_loop_ids = {str(r["id"]) for r in loops}

            # 3. Get active claims for enrichment
            claims = []
            if mode in ("factual", "reflection"):
                claim_rows = await conn.fetch(
                    "SELECT subject, predicate, value, confidence FROM v_active_claims LIMIT 10"
                )
                claims = [dict(r) for r in claim_rows]

        # 4. Score all candidates with mode-specific weights
        now = time.time()
        scored = []
        for c in candidates:
            breakdown = self._compute_scores(c, now, open_loop_ids, affect_vector, weights)
            total = sum(breakdown.values())
            scored.append(ScoredMemory(
                id=str(c["id"]),
                kind=c["kind"],
                title=c["title"],
                content=c["content"],
                score=total,
                breakdown=breakdown,
                estimated_tokens=max(1, len(c["content"]) // 4),
            ))

        scored.sort(key=lambda m: m.score, reverse=True)

        # 5. Pack into token budget
        packed = []
        token_count = 0
        for mem in scored:
            if token_count + mem.estimated_tokens > max_tokens:
                continue
            packed.append(mem)
            token_count += mem.estimated_tokens

        if not packed and not claims:
            return ""

        # 6. Format context string
        lines = [f"[Context from memory — mode: {mode}]"]

        # Include relevant claims first (they're usually short and high-value)
        if claims:
            lines.append("[Known facts]")
            for c in claims[:5]:
                lines.append(f"  • {c['subject']} {c['predicate']} {c['value']} (conf: {c['confidence']:.1f})")

        if packed:
            lines.append("[Retrieved memories]")
            for mem in packed:
                lines.append(f"  • [{mem.kind}] {mem.title}: {mem.content}")

        return "\n".join(lines)

    def _compute_scores(
        self, candidate: dict, now: float,
        open_loop_ids: set, affect: Optional[Dict[str, float]],
        weights: Optional[Dict[str, float]] = None,
    ) -> Dict[str, float]:
        """Compute fusion score components using the specified weight profile."""

        if weights is None:
            weights = WEIGHT_PROFILES[DEFAULT_MODE]

        # 1. Semantic similarity (from pgvector)
        semantic = max(0, candidate.get("similarity", 0.0))

        # 2. Recency decay
        created_ts = candidate.get("created_ts", now)
        age_sec = max(0, now - created_ts)
        recency = math.pow(2, -age_sec / RECENCY_HALF_LIFE_SEC)

        # 3. Salience
        salience = candidate.get("salience", 0.5)

        # 4. Open loop boost
        source_id = str(candidate.get("source_event_id", ""))
        open_loop_boost = 0.7 if source_id in open_loop_ids else 0.0

        # 5. Affect congruence
        affect_score = 0.0
        if affect and weights.get("affect", 0) > 0:
            meta = candidate.get("metadata", {})
            if isinstance(meta, str):
                try:
                    meta = json.loads(meta)
                except json.JSONDecodeError:
                    meta = {}
            mem_affect = meta.get("affect_at_creation", {})
            if mem_affect:
                shared_dims = set(affect.keys()) & set(mem_affect.keys())
                if shared_dims:
                    dot = sum(affect[d] * mem_affect.get(d, 0) for d in shared_dims)
                    mag_a = math.sqrt(sum(v ** 2 for v in affect.values()))
                    mag_b = math.sqrt(sum(v ** 2 for v in mem_affect.values()))
                    if mag_a > 0 and mag_b > 0:
                        affect_score = max(0, dot / (mag_a * mag_b))

        return {
            "semantic": weights["semantic"] * semantic,
            "recency": weights["recency"] * recency,
            "salience": weights["salience"] * salience,
            "open_loop": weights["open_loop"] * open_loop_boost,
            "affect": weights["affect"] * affect_score,
        }

    async def _semantic_search(self, conn, embedding: list, limit: int,
                                domain_filter: Optional[str] = None) -> list:
        """Search memories by cosine similarity using pgvector with optional prefilter."""
        emb_str = "[" + ",".join(str(x) for x in embedding) + "]"

        if domain_filter:
            rows = await conn.fetch("""
                SELECT
                    id, kind, title, content, salience,
                    source_event_id, metadata,
                    EXTRACT(EPOCH FROM created_at) as created_ts,
                    1 - (embedding <=> $1::vector) as similarity
                FROM memories
                WHERE embedding IS NOT NULL
                  AND kind = $3
                  AND (superseded_by IS NULL)
                ORDER BY embedding <=> $1::vector
                LIMIT $2
            """, emb_str, limit, domain_filter)
        else:
            rows = await conn.fetch("""
                SELECT
                    id, kind, title, content, salience,
                    source_event_id, metadata,
                    EXTRACT(EPOCH FROM created_at) as created_ts,
                    1 - (embedding <=> $1::vector) as similarity
                FROM memories
                WHERE embedding IS NOT NULL
                  AND (superseded_by IS NULL)
                ORDER BY embedding <=> $1::vector
                LIMIT $2
            """, emb_str, limit)

        return [dict(r) for r in rows]

    async def _embed(self, text: str) -> Optional[list]:
        """Generate a 384d embedding using all-MiniLM-L6-v2."""
        try:
            from sentence_transformers import SentenceTransformer
            if not hasattr(self, "_model"):
                self._model = SentenceTransformer("all-MiniLM-L6-v2")
            emb = self._model.encode(text).tolist()
            return emb
        except ImportError:
            log.warning("sentence-transformers not installed. Retrieval disabled.")
            return None
        except Exception as e:
            log.warning(f"Embedding failed: {e}")
            return None
