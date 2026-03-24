"""
apps/mindd/pulse_worker.py — Mind pulse generation engine

Reuses core.mind prompt building and core.mind_pulse parsing.
Generates structured MindPulse JSON from a configurable LLM endpoint.

Target: small sidecar model (0.6–4B via MLX on a separate port).
Fallback: main 35B on localhost:8088 (same as reflex, but only for testing).
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict
from typing import Optional

import httpx

from core.mind import (
    build_mind_prompt,
    process_mind_response,
    new_thought_id,
    PHASE_BUDGETS,
    PHASE_TEMPERATURES,
)
from core.mind_pulse import MindPulse

log = logging.getLogger("mindd.pulse_worker")

# Sidecar LLM endpoint — defaults to main 35B for testing,
# override with GARY_MINDD_LLM_URL to point at a dedicated small model.
GARY_MINDD_LLM_URL = os.getenv(
    "GARY_MINDD_LLM_URL",
    "http://localhost:8088/v1/chat/completions",
)

CONNECT_TIMEOUT = 5.0
READ_TIMEOUT = 30.0


async def generate_pulse(
    *,
    phase: str,
    recent_thoughts: list[str],
    avoid_topics: list[str],
    affect_summary: str,
    open_loops: list[str],
    recent_conversation: list[str],
    stale_streak: int = 0,
) -> dict:
    """Generate a single mind pulse via the sidecar LLM.

    Returns a dict with keys:
        thought_id, phase, clean_text, salience, initiative (or None),
        pulse (MindPulse dict or None)
    """
    thought_id = new_thought_id()
    messages = build_mind_prompt(
        phase=phase,
        recent_thoughts=recent_thoughts,
        avoid_topics=avoid_topics,
        affect_summary=affect_summary,
        open_loops=open_loops,
        recent_conversation=recent_conversation,
        json_mode=True,
        stale_streak=stale_streak,
    )

    max_tokens = PHASE_BUDGETS.get(phase, 200)
    base_temperature = PHASE_TEMPERATURES.get(phase, 0.6)
    temperature = min(1.5, base_temperature + (stale_streak * 0.15))

    payload = {
        "model": "default",
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "stream": False,
        "frequency_penalty": 0.8,
        "presence_penalty": 0.6,
    }

    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(CONNECT_TIMEOUT, read=READ_TIMEOUT)
        ) as client:
            resp = await client.post(GARY_MINDD_LLM_URL, json=payload)

            if resp.status_code != 200:
                log.error("Sidecar LLM HTTP %d: %s", resp.status_code, resp.text[:200])
                return {"error": f"HTTP {resp.status_code}", "thought_id": thought_id}

            data = resp.json()
            full_text = (
                data.get("choices", [{}])[0]
                .get("message", {})
                .get("content", "")
            )

            if not full_text.strip():
                return {"error": "empty_response", "thought_id": thought_id}

            clean_text, initiative, salience, pulse_obj = process_mind_response(
                full_text,
                thought_id,
                phase,
                json_mode=True,
            )

            result: dict = {
                "thought_id": thought_id,
                "phase": phase,
                "clean_text": clean_text,
                "salience": round(salience, 3),
                "initiative": None,
                "pulse": asdict(pulse_obj) if pulse_obj else None,
            }

            if initiative:
                result["initiative"] = {
                    "text": initiative.text,
                    "reason": initiative.reason,
                    "salience": round(initiative.salience, 3),
                }

            return result

    except httpx.ConnectError:
        log.error("Cannot reach sidecar LLM at %s", GARY_MINDD_LLM_URL)
        return {"error": "connect_failed", "thought_id": thought_id}
    except Exception as exc:
        log.exception("Pulse generation failed: %s", exc)
        return {"error": str(exc), "thought_id": thought_id}


async def check_sidecar_health() -> bool:
    """Returns True if the sidecar LLM is reachable."""
    url = GARY_MINDD_LLM_URL.rsplit("/v1/", 1)[0] + "/v1/models"
    try:
        async with httpx.AsyncClient(timeout=2.0) as c:
            r = await c.get(url)
            return r.status_code < 500
    except Exception:
        return False
