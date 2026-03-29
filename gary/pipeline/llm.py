"""
pipeline/llm.py — Async streaming LLM client for flash-moe / OpenAI-compat API

Connects to the mac_flash_moe inference server at http://localhost:8088
(same default as core/llm_watchdog.GARY_LLM_PORT; infer: --serve 8088)

Key features:
  - Fully async via httpx to avoid blocking the event loop even 1ms
  - Yields tokens as they arrive
  - Also yields {"sentence": "..."} events when a complete sentence is ready for TTS
  - Interrupt-safe: caller can send asyncio.CancelledError to stop generation

Usage:
    async for event in stream(messages, interrupt_event):
        if "token" in event:
            send_to_browser(event["token"])
        elif "sentence" in event:
            tts_queue.put(event["sentence"])
"""

import asyncio
import json
import logging
import re
from pathlib import Path
from typing import AsyncIterator

import httpx

log = logging.getLogger("gary.llm")

LLM_URL         = "http://localhost:8088/v1/chat/completions"
MAX_TOKENS      = 800        # Rich personal-assistant answers
TEMPERATURE     = 0.75
CONNECT_TIMEOUT = 3.0        # Fail fast if server not running

# Sentence boundary: end on . ! ? followed by space or end-of-string
_SENTENCE_END = re.compile(r'([.!?])\s+|([.!?])$')

_PROMPTS_DIR = Path(__file__).parent.parent / "core" / "prompts"
_SYSTEM_PROMPT_FILE = _PROMPTS_DIR / "system.txt"

def _load_system_prompt() -> str:
    """Load GARY's system prompt from core/prompts/system.txt."""
    try:
        return _SYSTEM_PROMPT_FILE.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        log.warning("core/prompts/system.txt not found — using inline fallback")
        return (
            "You are GARY — a brilliant, warm personal AI assistant. "
            "Speak naturally for voice output, no markdown, be concise."
        )

SYSTEM_PROMPT = _load_system_prompt()


def _build_payload(messages: list[dict], *, max_tokens: int, temperature: float) -> dict:
    """Construct streaming chat payload."""
    return {
        "model": "default",
        "messages": [{"role": "system", "content": SYSTEM_PROMPT}] + messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "stream": True,
    }



def _split_sentences(buffer: str) -> tuple[list[str], str]:
    """
    Split buffer into complete sentences and a leftover partial.
    Returns (complete_sentences, remainder).
    """
    sentences = []
    pos = 0
    for m in _SENTENCE_END.finditer(buffer):
        end = m.end()
        sentence = buffer[pos:end].strip()
        if sentence:
            sentences.append(sentence)
        pos = end
    remainder = buffer[pos:]
    return sentences, remainder


async def stream(
    messages: list[dict],
    interrupt: asyncio.Event | None = None,
    *,
    max_tokens: int | None = None,
    temperature: float | None = None,
) -> AsyncIterator[dict]:
    """
    Async generator streaming events from the LLM.
    Events:
      {"type": "token",    "text": "..."}   — individual token for display
      {"type": "sentence", "text": "..."}   — complete sentence ready for TTS
      {"type": "done"}                      — generation complete
      {"type": "error",   "message": "..."}
    """
    max_tokens = MAX_TOKENS if max_tokens is None else max_tokens
    temperature = TEMPERATURE if temperature is None else temperature
    payload = _build_payload(messages, max_tokens=max_tokens, temperature=temperature)

    buffer = ""
    in_think = False      # True while inside <think>...</think>
    think_buf = ""        # Accumulates raw think content for console logging

    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(CONNECT_TIMEOUT, read=300.0)) as client:
            async with client.stream("POST", LLM_URL, json=payload) as resp:
                if resp.status_code != 200:
                    body = await resp.aread()
                    yield {"type": "error", "message": f"LLM HTTP {resp.status_code}: {body[:200]}"}
                    return

                async for line in resp.aiter_lines():
                    # Check interrupt before each SSE line
                    if interrupt and interrupt.is_set():
                        log.info("LLM stream interrupted")
                        return

                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        break

                    try:
                        chunk = json.loads(data)
                    except json.JSONDecodeError:
                        continue

                    delta = (
                        chunk.get("choices", [{}])[0]
                             .get("delta", {})
                             .get("content", "")
                    )
                    if delta:
                        delta = delta.replace("\ufffd", "")
                    if not delta:
                        continue

                    # ── Think-block routing ───────────────────────────────
                    # We scan the incoming delta for <think> / </think> tags
                    # and route content accordingly.  Both tags may arrive as
                    # their own token, or embedded in a larger delta.
                    remaining = delta
                    while remaining:
                        if not in_think:
                            if "<think>" in remaining:
                                before, _, after = remaining.partition("<think>")
                                # Emit any real text before the opening tag
                                if before:
                                    buffer += before
                                    yield {"type": "token", "text": before}
                                    sentences, buffer = _split_sentences(buffer)
                                    for s in sentences:
                                        if s.strip():
                                            yield {"type": "sentence", "text": s.strip()}
                                in_think = True
                                think_buf = ""
                                remaining = after
                            else:
                                # Normal token — display + TTS pipeline
                                buffer += remaining
                                yield {"type": "token", "text": remaining}
                                sentences, buffer = _split_sentences(buffer)
                                for s in sentences:
                                    if s.strip():
                                        yield {"type": "sentence", "text": s.strip()}
                                remaining = ""
                        else:
                            # Inside think block
                            if "</think>" in remaining:
                                before, _, after = remaining.partition("</think>")
                                think_buf += before
                                # Emit full think block to console only
                                yield {"type": "think_token", "text": think_buf}
                                in_think = False
                                think_buf = ""
                                remaining = after
                            else:
                                think_buf += remaining
                                remaining = ""

        # Flush any trailing partial (e.g. response didn't end with punctuation)
        if in_think and think_buf.strip():
            yield {"type": "think_token", "text": think_buf}
        if buffer.strip():
            yield {"type": "sentence", "text": buffer.strip()}

        yield {"type": "done"}

    except httpx.ConnectError:
        yield {
            "type":    "error",
            "message": "LLM connection lost. The auto-restart watchdog is recovering the process...",
        }
    except asyncio.CancelledError:
        log.info("LLM stream cancelled")
        raise
    except Exception as exc:
        log.exception(f"LLM stream error: {exc}")
        yield {"type": "error", "message": str(exc)}


async def check_connectivity() -> bool:
    """Returns True if the LLM server is reachable."""
    try:
        async with httpx.AsyncClient(timeout=2.0) as c:
            r = await c.get("http://localhost:8088/v1/models")
            return r.status_code < 500
    except Exception:
        return False
