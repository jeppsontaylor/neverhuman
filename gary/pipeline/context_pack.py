"""
pipeline/context_pack.py — Reflex context compiler v2

v2 additions over v1:
  - DB-backed retrieval slots (active_loops, top_claims, top_question)
  - Retrieval audit integration (logs what was packed per turn)
  - Affect summary injection into context
  - Automatic MAX_HISTORY_TURNS reduction when pack is active

Fixed slots + stable hash for logging, retrieval audit, and reproducibility.
Prepend one system message before chat history; global persona stays in llm.SYSTEM_PROMPT.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
from typing import Any, Optional

log = logging.getLogger("gary.context_pack")

COMPILER_VERSION = 2

# ── Feature flags ────────────────────────────────────────────────────────────

def context_pack_enabled() -> bool:
    """Default ON; set GARY_CONTEXT_PACK=0 to disable."""
    val = os.getenv("GARY_CONTEXT_PACK", "1").strip().lower()
    return val not in ("0", "false", "no", "off")


def pack_history_limit() -> int:
    """When context pack is enabled, trim history to 10 turns (from 20).

    The pack already summarises key context, so raw history can shrink.
    """
    if context_pack_enabled():
        return 10
    return 20


# ── DB retrieval helpers (graceful when DB is unavailable) ───────────────────

async def _fetch_active_loops(conn, limit: int = 3) -> list[dict]:
    """Top open loops by priority."""
    try:
        rows = await conn.fetch(
            "SELECT summary, priority FROM open_loops "
            "WHERE status = 'open' "
            "ORDER BY priority DESC LIMIT $1",
            limit,
        )
        return [dict(r) for r in rows]
    except Exception as exc:
        log.debug("active_loops fetch failed: %s", exc)
        return []


async def _fetch_top_claims(conn, limit: int = 2) -> list[dict]:
    """Top validated claims by recency."""
    try:
        rows = await conn.fetch(
            "SELECT subject, predicate, value, confidence FROM claims "
            "WHERE epistemic_status = 'validated' "
            "ORDER BY accessed_at DESC NULLS LAST LIMIT $1",
            limit,
        )
        return [dict(r) for r in rows]
    except Exception as exc:
        log.debug("top_claims fetch failed: %s", exc)
        return []


async def _fetch_top_question(conn) -> Optional[str]:
    """Most salient open question."""
    try:
        row = await conn.fetchrow(
            "SELECT text FROM questions "
            "WHERE status = 'open' "
            "ORDER BY salience DESC LIMIT 1",
        )
        return row["text"] if row else None
    except Exception as exc:
        log.debug("top_question fetch failed: %s", exc)
        return None


async def _fetch_retrieval_slots(
    conn,
) -> dict[str, Any]:
    """Pull all DB-backed retrieval slots. Returns empty dicts on failure."""
    loops = await _fetch_active_loops(conn)
    claims = await _fetch_top_claims(conn)
    question = await _fetch_top_question(conn)
    return {
        "active_loops": loops,
        "top_claims": claims,
        "top_question": question,
    }


# ── Slot extraction ─────────────────────────────────────────────────────────

def _extract_history_slots(history: list[dict]) -> dict[str, Any]:
    """Extract slots from conversation history (no DB needed)."""
    users = [m for m in history if m.get("role") == "user"]
    assistants = [m for m in history if m.get("role") == "assistant"]

    last_user = ""
    last_user_i = -1
    for i, m in enumerate(history):
        if m.get("role") == "user":
            last_user_i = i
            last_user = (m.get("content") or "")[:4000]

    prior_assistant = ""
    if last_user_i > 0:
        for j in range(last_user_i - 1, -1, -1):
            if history[j].get("role") == "assistant":
                prior_assistant = (history[j].get("content") or "")[:1500]
                break

    initiative_note = ""
    for m in history:
        c = m.get("content") or ""
        if m.get("role") == "system" and "INTERNAL DIRECTIVE" in c:
            initiative_note = c[:500]
            break

    return {
        "v": COMPILER_VERSION,
        "last_user_excerpt": last_user[:800],
        "prior_assistant_excerpt": prior_assistant[:600],
        "user_turn_count": len(users),
        "assistant_turn_count": len(assistants),
        "has_system_initiative": bool(initiative_note),
        "initiative_excerpt": initiative_note[:400] if initiative_note else "",
    }


# ── Pack body rendering ─────────────────────────────────────────────────────

def _pack_body(
    slots: dict[str, Any],
    *,
    db_slots: Optional[dict[str, Any]] = None,
    affect_summary: str = "",
) -> str:
    lines = [
        f"[Context pack v{COMPILER_VERSION} — do not read labels aloud; use for grounding only]",
        f"LAST_USER: {slots['last_user_excerpt'] or '(none)'}",
        f"PRIOR_ASSISTANT: {slots['prior_assistant_excerpt'] or '(none)'}",
    ]

    # Affect summary injection
    if affect_summary:
        lines.append(f"AFFECT: {affect_summary}")

    # DB-backed retrieval slots
    if db_slots:
        loops = db_slots.get("active_loops", [])
        if loops:
            lines.append("OPEN_LOOPS:")
            for loop in loops:
                p = loop.get("priority", 0.5)
                lines.append(f"  • [{p:.1f}] {loop.get('summary', '?')}")

        claims = db_slots.get("top_claims", [])
        if claims:
            lines.append("KNOWN_FACTS:")
            for c in claims:
                conf = c.get("confidence", 0.5)
                lines.append(
                    f"  • {c.get('subject', '?')} {c.get('predicate', '?')} "
                    f"{c.get('value', '?')} (conf: {conf:.1f})"
                )

        question = db_slots.get("top_question")
        if question:
            lines.append(f"TOP_QUESTION: {question}")

    if slots.get("initiative_excerpt"):
        lines.append(f"INITIATIVE_CONTEXT: {slots['initiative_excerpt']}")

    lines.append(
        f"TURNS: user={slots['user_turn_count']} assistant={slots['assistant_turn_count']}"
    )
    lines.append("Chronological messages follow.")
    return "\n".join(lines)


# ── Public API ───────────────────────────────────────────────────────────────

def compile_reflex_context(
    history: list[dict],
    *,
    enabled: bool | None = None,
    db_slots: Optional[dict[str, Any]] = None,
    affect_summary: str = "",
) -> tuple[list[dict], str]:
    """
    Returns (messages_for_llm, pack_hash_hex20).

    When disabled, returns (history copy, "").
    When enabled, returns ([pack_system] + history, hash).

    Args:
        history: Full conversation history
        enabled: Force on/off (defaults to env check)
        db_slots: Pre-fetched DB retrieval slots from _fetch_retrieval_slots()
        affect_summary: Current affect state string (e.g. "curiosity=0.8 (high)")
    """
    if enabled is None:
        enabled = context_pack_enabled()
    hist = list(history)
    if not enabled or not hist:
        return hist, ""

    slots = _extract_history_slots(hist)

    # Merge DB slots into canonical representation for hashing
    canonical_data = dict(slots)
    if db_slots:
        canonical_data["db"] = db_slots
    if affect_summary:
        canonical_data["affect"] = affect_summary

    canonical = json.dumps(canonical_data, sort_keys=True, default=str)
    pack_hash = hashlib.sha256(canonical.encode()).hexdigest()[:20]

    body = _pack_body(slots, db_slots=db_slots, affect_summary=affect_summary)
    pack_msg = {
        "role": "system",
        "content": body + f"\nPACK_HASH: {pack_hash}",
    }
    return [pack_msg] + hist, pack_hash


async def compile_reflex_context_with_db(
    history: list[dict],
    *,
    affect_summary: str = "",
    turn_id: str = "",
) -> tuple[list[dict], str]:
    """
    Full async version that fetches DB slots + logs retrieval audit.

    Uses graceful fallback: if DB is unavailable, compiles without DB slots.
    """
    if not context_pack_enabled():
        return list(history), ""

    db_slots = None
    retrieved_ids: list[str] = []

    try:
        from memory.db import get_pool
        pool = await get_pool()
        async with pool.acquire() as conn:
            db_slots = await _fetch_retrieval_slots(conn)
    except Exception as exc:
        log.debug("DB unavailable for context pack: %s", exc)

    messages, pack_hash = compile_reflex_context(
        history,
        enabled=True,
        db_slots=db_slots,
        affect_summary=affect_summary,
    )

    # Log retrieval audit (fire and forget)
    if pack_hash and db_slots:
        # Collect IDs of what was retrieved
        slot_counts = {}
        loops = db_slots.get("active_loops", [])
        claims = db_slots.get("top_claims", [])
        question = db_slots.get("top_question")
        slot_counts = {
            "active_loops": len(loops),
            "top_claims": len(claims),
            "top_question": 1 if question else 0,
        }

        try:
            from memory.db import get_pool
            from memory.retrieval_audit import log_retrieval_audit
            pool = await get_pool()
            async with pool.acquire() as conn:
                await log_retrieval_audit(
                    conn,
                    pack_hash=pack_hash,
                    turn_id=turn_id,
                    retrieved_ids=retrieved_ids,
                    slot_counts=slot_counts,
                )
        except Exception as exc:
            log.debug("Retrieval audit log failed: %s", exc)

    return messages, pack_hash
