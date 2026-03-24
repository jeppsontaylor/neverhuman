"""
apps/mindd/serve.py — Mind Daemon sidecar HTTP server

Runs as a separate process from server.py. Accepts pulse requests,
generates structured cognition via a small sidecar LLM, and returns
MindPulse JSON.

Port: 7863 (env: GARY_MINDD_PORT)
LLM: configurable via GARY_MINDD_LLM_URL (default: localhost:8088)

Usage:
    python -m apps.mindd.serve
    # or: uvicorn apps.mindd.serve:app --port 7863
"""
from __future__ import annotations

import asyncio
import logging
import os

import uvicorn
from fastapi import FastAPI, Request
from pydantic import BaseModel

from apps.mindd.pulse_worker import generate_pulse, check_sidecar_health

GARY_MINDD_PORT = int(os.getenv("GARY_MINDD_PORT", "7863"))

logging.basicConfig(
    level=os.getenv("MINDD_LOG_LEVEL", "INFO"),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("mindd.serve")

app = FastAPI(title="GARY Mind Daemon (mindd)")


# ── Request/Response models ──────────────────────────────────────────────────

class PulseRequest(BaseModel):
    phase: str
    recent_thoughts: list[str] = []
    avoid_topics: list[str] = []
    affect_summary: str = ""
    open_loops: list[str] = []
    recent_conversation: list[str] = []
    stale_streak: int = 0


class PulseResponse(BaseModel):
    thought_id: str
    phase: str = ""
    clean_text: str = ""
    salience: float = 0.0
    initiative: dict | None = None
    pulse: dict | None = None
    error: str | None = None


# ── Endpoints ────────────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    llm_ok = await check_sidecar_health()
    return {"status": "ok", "sidecar_llm": llm_ok}


async def _wait_client_disconnect(request: Request) -> None:
    """Completes when the caller closes the HTTP connection (voice preempt)."""
    while True:
        if await request.is_disconnected():
            return
        await asyncio.sleep(0.05)


@app.post("/pulse", response_model=PulseResponse)
async def pulse(req: PulseRequest, request: Request):
    """Generate a single mind pulse.

    Called by server.py when GARY_MIND_REMOTE=1.
    Returns a structured MindPulse or an error.

    If the client disconnects (e.g. user spoke and server cancelled the request),
    the in-flight LLM call is cancelled so the shared inference server can serve voice.
    """
    gen_task = asyncio.create_task(
        generate_pulse(
            phase=req.phase,
            recent_thoughts=req.recent_thoughts,
            avoid_topics=req.avoid_topics,
            affect_summary=req.affect_summary,
            open_loops=req.open_loops,
            recent_conversation=req.recent_conversation,
            stale_streak=req.stale_streak,
        )
    )
    disc_task = asyncio.create_task(_wait_client_disconnect(request))
    done, pending = await asyncio.wait(
        {gen_task, disc_task},
        return_when=asyncio.FIRST_COMPLETED,
    )
    for t in pending:
        t.cancel()
    for t in pending:
        try:
            await t
        except asyncio.CancelledError:
            pass

    if disc_task in done:
        if not gen_task.done():
            gen_task.cancel()
        try:
            await gen_task
        except asyncio.CancelledError:
            pass
        log.info("pulse cancelled — client disconnected (voice preempt)")
        return PulseResponse(
            thought_id="cancelled",
            error="client_disconnect",
        )

    disc_task.cancel()
    try:
        await disc_task
    except asyncio.CancelledError:
        pass

    try:
        result = gen_task.result()
    except Exception as exc:
        log.exception("generate_pulse failed: %s", exc)
        return PulseResponse(thought_id="error", error=str(exc))
    return PulseResponse(**result)


# ── Entry point ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    log.info("mindd starting on port %d", GARY_MINDD_PORT)
    uvicorn.run(
        "apps.mindd.serve:app",
        host="127.0.0.1",
        port=GARY_MINDD_PORT,
        log_level="info",
        reload=False,
        workers=1,
    )
