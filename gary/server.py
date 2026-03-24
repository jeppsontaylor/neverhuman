"""
server.py — NeverHuman / GARY unified server (port 7861)

WebSocket endpoint:
  /ws/gary   — full voice agent pipeline (hands-free, auto-VAD)
               PCM → VAD → ASR → LLM streaming → TTS → audio back

State per GARY connection:
  - Conversation history (sliding window, max 20 turns)
  - asyncio.Event  interrupt flag (set by {"type":"interrupt"} or {"type":"interrupt_hint"})
  - VADAccumulator (fires on detected utterance)
  - is_speaking flag for barge-in detection

Audio flow over WebSocket:
  Browser → Server:  binary frames = Float32 PCM 16kHz mono (raw AudioWorklet output)
                     or JSON text  = {"type":"interrupt"} | {"type":"interrupt_hint"} | {"type":"stop"} | {"type":"clear"}
                     (interrupt_hint = client stopped playback early; same server cancel as interrupt)
  Server → Browser:  JSON text = {"type": "transcript"|"token"|"state"|"error"|"health"|"typing", ...}
                     binary    = WAV bytes for TTS audio playback
"""

import asyncio
import gc
import json
import logging
import os
import re
import time
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

import httpx
import numpy as np
import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from starlette.websockets import WebSocketState

# ── pipeline imports ──────────────────────────────────────────────────────────
from pipeline import asr, tts
from pipeline.context_pack import compile_reflex_context, pack_history_limit
from pipeline.llm import stream as llm_stream, check_connectivity
from pipeline.vad import VADAccumulator, RollingBuffer, SpeechDetector, BARGEIN_PROB, BARGEIN_FRAMES
from pipeline.filler_audio import get_filler_wav_bytes
from pipeline.turn_classifier import TurnMode, classify_turn
from pipeline.turn_supervisor import TurnSupervisor, FloorState, FloorOwner, Engagement, Event

# ── mind daemon imports ──────────────────────────────────────────────────────
from core.mind import (
    select_phase, build_mind_prompt, format_affect_summary,
    process_mind_response, new_thought_id,
    ThoughtDeduplicator, InitiativeRateLimiter,
    mind_json_enabled,
    PHASE_BUDGETS, PHASE_COOLDOWNS, PHASE_TEMPERATURES, PHASE_LABELS,
)
from core.affect_types import AffectVector
from core.llm_watchdog import watchdog, WatchdogState
from core.rumination_governor import RuminationGovernor
from core.session_logger import SessionLogger, find_latest_log, is_enabled as _session_log_enabled
from core.log_writer import append as _log_append

# ── Config ─────────────────────────────────────────────────────────────────────
PORT             = int(os.getenv("GARY_PORT", "7861"))
LOG_LEVEL        = os.getenv("GARY_LOG_LEVEL", "INFO")
MAX_HISTORY_TURNS = pack_history_limit()  # 20 default; 10 when GARY_CONTEXT_PACK=1
IDLE_TIMEOUT_SEC  = 300  # Unload ASR/TTS after 5 min idle (saves ~1.55 GB)
_MIN_FREE_RAM_WARN_GB = 3.0  # Warn in health endpoint if free RAM drops below this

logging.basicConfig(level=LOG_LEVEL, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("gary.server")

# ── App ────────────────────────────────────────────────────────────────────────
app = FastAPI(title="GARY")

STATIC_DIR = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/")
async def index():
    """Serve index.html, or redirect to /setup if models are not ready."""
    from pipeline.model_manager import all_defaults_ready
    if not all_defaults_ready():
        from fastapi.responses import RedirectResponse
        return RedirectResponse("/setup")
    return FileResponse(str(STATIC_DIR / "index.html"))


@app.get("/setup")
async def setup_page():
    """Serve the first-run model setup wizard."""
    return FileResponse(str(STATIC_DIR / "setup.html"))


@app.get("/api/setup/status")
async def api_setup_status():
    """Return detection status for all models + flash-moe binary."""
    from fastapi.responses import JSONResponse
    from pipeline.model_manager import get_full_status
    return JSONResponse(get_full_status())


@app.get("/api/setup/models")
async def api_setup_models():
    """Return the full model catalog for the setup wizard dropdowns."""
    from fastapi.responses import JSONResponse
    from pipeline.model_manager import get_catalog
    return JSONResponse(get_catalog())


@app.post("/api/setup/download/{model_id}")
async def api_setup_download(model_id: str, hf_token: str | None = None):
    """Start downloading a model; returns an SSE stream with progress events."""
    from fastapi.responses import StreamingResponse
    from pipeline.model_manager import download_model_sse
    return StreamingResponse(
        download_model_sse(model_id, hf_token=hf_token),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/health")
async def health():
    llm_ok = await check_connectivity()
    return {
        "status":   "ok",
        "tts":      tts.is_available(),
        "llm_live": llm_ok,
    }


# ── Startup ─────────────────────────────────────────────────────────────────────
default_idle_timeout = IDLE_TIMEOUT_SEC


def _free_ram_gb() -> float:
    """Return available RAM in GB (free + reclaimable inactive pages)."""
    try:
        import subprocess
        out = subprocess.check_output(["vm_stat"], text=True)
        page_size = 16384  # Apple Silicon default
        free = inactive = 0
        for line in out.splitlines():
            if "Pages free" in line:
                free = int(line.split()[-1].rstrip("."))
            elif "Pages inactive" in line:
                inactive = int(line.split()[-1].rstrip("."))
        return (free + inactive) * page_size / 1e9
    except Exception:
        return -1.0


async def _idle_unload_task():
    """Background task: unload ASR/TTS after IDLE_TIMEOUT_SEC of inactivity."""
    while True:
        await asyncio.sleep(60)  # check every 60 seconds
        now = time.monotonic()
        if asr.is_loaded() and now - asr.last_use_time() > IDLE_TIMEOUT_SEC:
            log.info(f"[idle] ASR idle for >{IDLE_TIMEOUT_SEC}s — unloading")
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, asr.unload)
        if tts.is_loaded() and now - tts.last_use_time() > IDLE_TIMEOUT_SEC:
            log.info(f"[idle] TTS idle for >{IDLE_TIMEOUT_SEC}s — unloading")
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, tts.unload)


# ── Global state ───────────────────────────────────────────────────────────────
active_websockets: set[WebSocket] = set()

async def broadcast_health():
    """Push current LLM/TTS/ASR health to all connected clients."""
    if not active_websockets:
        return
    llm_ok = watchdog.is_healthy
    msg = {
        "type":       "health",
        "llm_ok":     llm_ok,
        "watchdog":   watchdog.state.value,
        "tts_ok":     tts.is_available(),
        "asr_loaded": asr.is_loaded(),
        "tts_loaded": tts.is_loaded(),
        "free_ram_gb": round(_free_ram_gb(), 1),
    }
    log_msg = f"Health: LLM={'online' if llm_ok else ('[WATCHDOG: ' + watchdog.state.value.upper() + ']')} · RAM={_free_ram_gb():.1f}GB free"
    
    # Broadcast to all
    dead = set()
    for ws in active_websockets:
        try:
            await ws.send_json(msg)
            # Also send a pipeline log update
            await ws.send_json({
                "type": "pipeline_log",
                "source": "sys",
                "text": log_msg,
                "append": False,
                "ts": time.time(),
            })
        except Exception:
            dead.add(ws)
            
    for ws in dead:
        active_websockets.discard(ws)

@app.on_event("startup")
async def startup():
    # ASR and TTS now load lazily on first use — no blocking loads at startup.
    # This reduces startup RAM from ~1.6 GB to ~100 MB.
    log.info("GARY starting (ASR + TTS will lazy-load on first voice request)…")

    # Start idle-unload watchdog
    asyncio.create_task(_idle_unload_task())

    # Start self-healing LLM watchdog — will auto-start infer if not running
    watchdog.on_state_change(lambda _: broadcast_health())
    await watchdog.start()
    log.info("LLM watchdog started ✓ (auto-restart enabled)")

    llm_ok = await check_connectivity()
    if llm_ok:
        log.info("LLM server reachable ✓")
    else:
        log.info(
            "LLM not yet reachable — watchdog will auto-start it"
        )

    log.info("GARY ready ✓")


# ── REST: voice list ──────────────────────────────────────────────────────────
@app.get("/api/voices")
async def api_voices():
    from fastapi.responses import JSONResponse
    return JSONResponse({
        "voices":  tts.get_voices(),
        "current": tts.get_voice(),
    })


@app.get("/api/memory_status")
async def api_memory_status():
    """Returns current model load state and available RAM."""
    from fastapi.responses import JSONResponse
    return JSONResponse({
        "asr_loaded":   asr.is_loaded(),
        "tts_loaded":   tts.is_loaded(),
        "free_ram_gb":  round(_free_ram_gb(), 1),
        "mem_warn":     _free_ram_gb() < _MIN_FREE_RAM_WARN_GB,
    })


# ── REST: session log download ────────────────────────────────────────────────
@app.get("/api/logs/latest/{log_type}")
async def api_download_latest_log(log_type: str):
    """Download the most recent session log (condensed or detailed)."""
    if log_type not in ("condensed", "detailed"):
        from fastapi.responses import JSONResponse
        return JSONResponse({"error": "log_type must be 'condensed' or 'detailed'"}, status_code=400)
    path = find_latest_log(log_type)
    if path is None or not path.exists():
        from fastapi.responses import JSONResponse
        return JSONResponse({"error": f"No {log_type} log found"}, status_code=404)
    return FileResponse(
        str(path),
        media_type="application/x-ndjson",
        filename=path.name,
        headers={"Content-Disposition": f'attachment; filename="{path.name}"'},
    )


# ── REST: persistent log viewer ──────────────────────────────────────────────
from core.log_writer import LOGS_DIR as _LOGS_DIR

_LOG_NAMES = {"conversation", "mind_stream", "websocket"}


@app.get("/logs/api/{name}")
async def api_get_log(name: str, offset: int = 0):
    """Return raw log text starting from byte offset; header X-File-Size for next poll."""
    if name not in _LOG_NAMES:
        from fastapi.responses import JSONResponse
        return JSONResponse({"error": "invalid log name"}, status_code=400)
    path = _LOGS_DIR / f"{name}.log"
    if not path.exists():
        from fastapi.responses import PlainTextResponse
        return PlainTextResponse("", headers={"X-File-Size": "0"})
    size = path.stat().st_size
    from fastapi.responses import PlainTextResponse
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        if offset > 0:
            f.seek(min(offset, size))
        content = f.read()
    return PlainTextResponse(content, headers={"X-File-Size": str(size)})


@app.get("/logs/")
async def logs_viewer():
    """Self-contained dark-themed HTML page for viewing the 3 log files in real time."""
    from fastapi.responses import HTMLResponse
    html = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>GARY · Log Viewer</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;600&display=swap" rel="stylesheet">
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{background:#070a14;color:#e2e8f0;font-family:'JetBrains Mono',monospace;height:100vh;display:flex;flex-direction:column}
.top-bar{display:flex;align-items:center;gap:16px;padding:12px 20px;border-bottom:1px solid rgba(255,255,255,0.07);background:rgba(0,0,0,0.4);flex-shrink:0}
.top-title{font-size:11px;font-weight:700;letter-spacing:0.14em;text-transform:uppercase;color:#475569}
.cards{display:flex;gap:8px}
.card{padding:6px 14px;border-radius:8px;border:1px solid rgba(255,255,255,0.1);background:rgba(255,255,255,0.03);font-size:11px;cursor:pointer;transition:all 0.2s;color:#94a3b8}
.card:hover{background:rgba(255,255,255,0.07)}
.card.active{color:#e2e8f0;border-color:rgba(139,92,246,0.5);background:rgba(139,92,246,0.12)}
.actions{margin-left:auto;display:flex;gap:6px}
.act-btn{padding:4px 12px;border-radius:6px;border:1px solid rgba(255,255,255,0.1);background:rgba(255,255,255,0.04);font-size:10px;color:#94a3b8;cursor:pointer;font-family:inherit;transition:all 0.2s;text-decoration:none}
.act-btn:hover{background:rgba(255,255,255,0.1);color:#e2e8f0}
.viewer{flex:1;overflow-y:auto;padding:16px 20px;font-size:11px;line-height:1.7;white-space:pre-wrap;word-break:break-word;color:#94a3b8}
.viewer::-webkit-scrollbar{width:4px}
.viewer::-webkit-scrollbar-thumb{background:rgba(139,92,246,0.3);border-radius:4px}
.empty{color:#475569;font-style:italic;padding:40px;text-align:center}
.status{font-size:9px;color:#475569;letter-spacing:0.06em}
</style>
</head>
<body>
<div class="top-bar">
  <span class="top-title">Log Viewer</span>
  <div class="cards">
    <div class="card active" data-log="conversation">Conversation</div>
    <div class="card" data-log="mind_stream">Mind Stream</div>
    <div class="card" data-log="websocket">WebSocket</div>
  </div>
  <div class="actions">
    <button class="act-btn" id="copy-btn">Copy All</button>
    <a class="act-btn" id="dl-btn" download>Download</a>
  </div>
  <span class="status" id="status">idle</span>
</div>
<pre class="viewer" id="viewer"><span class="empty">Select a log to begin viewing…</span></pre>
<script>
const viewer = document.getElementById('viewer');
const status = document.getElementById('status');
const dlBtn = document.getElementById('dl-btn');
let activeName = 'conversation';
let offset = 0;
let polling = null;

function switchLog(name) {
  activeName = name;
  offset = 0;
  viewer.textContent = '';
  document.querySelectorAll('.card').forEach(c => c.classList.toggle('active', c.dataset.log === name));
  dlBtn.href = '/logs/api/' + name;
  dlBtn.download = name + '.log';
  fetchLog();
}

async function fetchLog() {
  try {
    const r = await fetch('/logs/api/' + activeName + '?offset=' + offset);
    const text = await r.text();
    const newSize = parseInt(r.headers.get('X-File-Size') || '0', 10);
    if (text) {
      viewer.textContent += text;
      if (viewer.scrollHeight - viewer.scrollTop - viewer.clientHeight < 120) {
        viewer.scrollTop = viewer.scrollHeight;
      }
    }
    offset = newSize;
    status.textContent = 'offset: ' + offset + 'B';
  } catch(e) {
    status.textContent = 'fetch error';
  }
}

document.querySelectorAll('.card').forEach(c => {
  c.addEventListener('click', () => switchLog(c.dataset.log));
});

document.getElementById('copy-btn').addEventListener('click', () => {
  navigator.clipboard.writeText(viewer.textContent).then(() => {
    const btn = document.getElementById('copy-btn');
    btn.textContent = 'Copied!';
    setTimeout(() => btn.textContent = 'Copy All', 1500);
  });
});

switchLog('conversation');
polling = setInterval(fetchLog, 2000);
</script>
</body>
</html>"""
    return HTMLResponse(html)



async def _safe_send_json(ws: WebSocket, payload: dict):
    try:
        if ws.client_state == WebSocketState.CONNECTED:
            await ws.send_text(json.dumps(payload))
    except Exception:
        pass


async def _safe_send_bytes(ws: WebSocket, data: bytes):
    try:
        if ws.client_state == WebSocketState.CONNECTED:
            await ws.send_bytes(data)
    except Exception:
        pass


async def _pipeline_log(ws: WebSocket, service: str, text: str, *, append: bool = False):
    """Live console line for the browser (ASR / LLM / TTS / sys)."""
    # Only log discrete events to file; skip per-token appends to keep logs clean
    if not append:
        _log_append("websocket", f"{service.upper()}: {text}")
    await _safe_send_json(
        ws,
        {
            "type": "log",
            "service": service,
            "text": text,
            "append": append,
            "ts": time.time(),
        },
    )


def _trim_history(history: list[dict]) -> list[dict]:
    """Keep last MAX_HISTORY_TURNS pairs."""
    max_msgs = MAX_HISTORY_TURNS * 2
    return history[-max_msgs:] if len(history) > max_msgs else history


def _same_llm_origin(a: str, b: str) -> bool:
    """True if two OpenAI-compat URLs target the same scheme/host/port."""

    def _norm(p):
        h = (p.hostname or "").lower()
        if h == "localhost":
            h = "127.0.0.1"
        port = p.port
        if port is None:
            port = 443 if (p.scheme or "").lower() == "https" else 80
        return ((p.scheme or "http").lower(), h, port)

    try:
        return _norm(urlparse(a)) == _norm(urlparse(b))
    except Exception:
        return False


_MD_BOLD_ITALIC = re.compile(r'\*{1,3}|_{1,3}')
_MD_HEADER      = re.compile(r'^#{1,6}\s+', re.MULTILINE)
_MD_BRACKET     = re.compile(r'[\[\]]')
_MD_BACKTICK    = re.compile(r'`+')
_MD_LINK        = re.compile(r'\[([^\]]*)\]\([^)]*\)')   # [text](url) → text
_MD_TRIPLE_DASH = re.compile(r'-{2,}')                   # --- / -- → pause
_MD_ANGLE       = re.compile(r'<[^>]+>')                  # leftover HTML tags

def _clean_for_voice(text: str) -> str:
    """
    Strip markdown and symbol noise so TTS only receives clean spoken English.
    Also applied to token display.
    NOTE: intentionally does NOT strip leading/trailing whitespace, because tokens
    from the LLM stream carry meaningful leading spaces between words.
    """
    if not text:
        return text
    # Markdown links: keep the link text
    text = _MD_LINK.sub(r'\1', text)
    # Headers: strip the #s
    text = _MD_HEADER.sub('', text)
    # Bold/italic markers: *** ** * _ __ ___
    text = _MD_BOLD_ITALIC.sub('', text)
    # Backticks (inline code)
    text = _MD_BACKTICK.sub('', text)
    # Square brackets used in poem formatting, citations, etc.
    text = _MD_BRACKET.sub('', text)
    # Remaining HTML-like tags
    text = _MD_ANGLE.sub('', text)
    # em-dash / en-dash rendered as --- or -- → natural pause comma
    text = _MD_TRIPLE_DASH.sub(',', text)
    # Collapse multiple newlines to a single space (but preserve single spaces)
    text = re.sub(r'\n+', ' ', text)
    # Collapse runs of 3+ spaces (never strip single spaces)
    text = re.sub(r'   +', '  ', text)
    return text  # no .strip() — leading spaces are intentional in token streams


# ── Silent stub for old browser tabs that retry /ws/transcribe ────────────────
@app.websocket("/ws/transcribe")
async def ws_transcribe_stub(websocket: WebSocket):
    """Silently close — transcribe tab removed in redesign."""
    await websocket.close(code=1001)


# ── GARY voice agent ──────────────────────────────────────────────────────────
@app.websocket("/ws/gary")
async def ws_gary(websocket: WebSocket):
    await websocket.accept()
    active_websockets.add(websocket)
    log.info(f"[gary] connected: {websocket.client}")
    await _pipeline_log(websocket, "sys", "WebSocket connected · pipeline log live")

    # ── Session logger (opt-in via GARY_SESSION_LOG=1) ──────────────────────
    import uuid as _uuid
    _sess_id = _uuid.uuid4().hex[:12]
    slog = SessionLogger(session_id=_sess_id)
    await slog.start()
    slog.log("session_start", "sys", {
        "client": str(websocket.client),
        "session_log_enabled": _session_log_enabled(),
    })
    slog.log_condensed("system", "connected", f"Session {_sess_id} started", {
        "client": str(websocket.client),
    })
    _sess_t0 = time.monotonic()

    history: list[dict] = []
    interrupt_event = asyncio.Event()
    current_task: asyncio.Task | None = None
    last_activity: float = time.monotonic()  # tracks idle time for mind daemon
    llm_gate = asyncio.Lock()  # shared lock: voice has priority, mind yields
    mind_interrupt = asyncio.Event()  # separate interrupt for mind daemon
    affect = AffectVector()  # in-RAM emotional state
    is_speaking = False   # True while TTS audio is actively queued/playing
    is_speaking_since: float = 0.0   # v3.2: monotonic timestamp for failsafe
    IS_SPEAKING_TIMEOUT = 30.0       # v3.2: hard cap — 30s max speaking time
    turn_epoch: int = 0   # tracks the current audio epoch for v3.1 frontend barrier
    current_turn_mode: TurnMode = TurnMode.LAYERED  # v4: turn classification

    # ── v5: Attention Kernel (TurnSupervisor) ─────────────────────────────────
    utterance_queue_placeholder: asyncio.Queue = asyncio.Queue()  # will be set below
    supervisor = TurnSupervisor(utterance_queue_placeholder)
    initiative_queue: asyncio.Queue = asyncio.Queue()  # dedicated queue for mind initiatives

    # Broadcast initial health
    await broadcast_health()

    async def _set_state(state: str, extra: dict | None = None):
        payload = {"type": "state", "state": state}
        if extra:
            payload.update(extra)
        await _safe_send_json(websocket, payload)

    async def handle_utterance(audio_np: Optional[np.ndarray] = None, text_override: Optional[str] = None):
        """Full pipeline: ASR (or text bypass) → LLM stream → TTS → audio."""
        nonlocal history, current_task, is_speaking, last_activity, turn_epoch
        last_activity = time.monotonic()
        supervisor._touch_human_activity()
        mind_interrupt.set()  # signal mind daemon to yield

        # Barge-in: cancel any in-progress generation / TTS
        interrupt_event.set()
        if current_task and not current_task.done():
            current_task.cancel()
            try:
                await asyncio.wait_for(asyncio.shield(current_task), timeout=0.15)
            except Exception:
                pass
        interrupt_event.clear()
        is_speaking = False

        # Signal browser to stop playing previous audio
        turn_epoch += 1
        await _safe_send_json(websocket, {"type": "stop_audio", "epoch": turn_epoch})

        if text_override:
            text = text_override
            log.info(f"[gary] system initiative: {text!r}")
            await _pipeline_log(websocket, "sys", f"Taking initiative: {text}")
            _log_append("conversation", f"GARY (initiative): {text}")
            slog.log("initiative_utterance", "mind", {"text": text}, turn=turn_epoch)
            history.append({"role": "system", "content": f"INTERNAL DIRECTIVE: You must take the following initiative right now: {text}"})
        else:
            # ── ASR ──────────────────────────────────────────────────────────────
            await _set_state("thinking")
            n_samp = int(audio_np.shape[0])
            dur = n_samp / 16000.0
            await _pipeline_log(
                websocket,
                "asr",
                f"Transcribing {dur:.1f}s audio ({n_samp} samples @ 16kHz)…",
            )
            t0 = time.time()
            text = await asr.transcribe(audio_np)
            asr_ms = int((time.time() - t0) * 1000)

            if not text:
                await _pipeline_log(websocket, "asr", "(empty transcript — back to listening)")
                supervisor.on_empty_transcript()  # v5: release the floor!
                await _set_state("listening")
                return

            log.info(f"[gary] user: {text!r}")
            _log_append("conversation", f"User: {text}")
            preview = text if len(text) <= 220 else text[:217] + "…"
            await _pipeline_log(websocket, "asr", f"Transcript ({asr_ms}ms): {preview}")
            await _safe_send_json(websocket, {"type": "transcript", "text": text})

            # v5: Floor -> ASR_PENDING (blocks mind daemon while we think)
            import uuid
            supervisor.on_transcript(uuid.uuid4())

            # ── Session log: ASR result ──
            slog.log("transcript", "asr", {
                "text": text, "asr_ms": asr_ms,
                "audio_duration_sec": round(dur, 2),
            }, turn=turn_epoch)
            slog.log_condensed("user", "said", text, {
                "asr_ms": asr_ms,
                "audio_duration_sec": round(dur, 2),
            }, turn=turn_epoch)

            # v4: Classify turn complexity
            current_turn_mode = classify_turn(text)
            await _pipeline_log(
                websocket, "sys",
                f"Turn mode: {current_turn_mode.value}",
            )
            await _safe_send_json(websocket, {
                "type": "turn_mode",
                "mode": current_turn_mode.value,
            })
            slog.log("turn_mode", "sys", {"mode": current_turn_mode.value}, turn=turn_epoch)

            # Pre-baked WAV only for non-snap turns (snap should be fast enough)
            if current_turn_mode != TurnMode.SNAP:
                filler = get_filler_wav_bytes(STATIC_DIR)
                if filler and not interrupt_event.is_set():
                    await _safe_send_json(websocket, {"type": "audio_start", "epoch": turn_epoch})
                    await _safe_send_bytes(websocket, filler)

            history.append({"role": "user", "content": text})
        history = _trim_history(history)

        # Emit a typing indicator so the UI can animate immediately
        await _safe_send_json(websocket, {"type": "typing"})

        # ── LLM + TTS pipeline (cancellable co-routine) ────────────────────
        async def _generate_and_speak():
            nonlocal is_speaking, history, last_activity, turn_epoch
            assistant_text = ""
            llm_line_open = False
            llm_t0 = time.time()
            first_token = True

            # Ensure frontend accepts audio for this epoch
            await _safe_send_json(websocket, {"type": "audio_start", "epoch": turn_epoch})

            staged_history, pack_hash = compile_reflex_context(history)
            if pack_hash:
                await _pipeline_log(
                    websocket,
                    "llm",
                    f"context_pack v1 hash={pack_hash}",
                    append=False,
                )
            slog.log("llm_start", "llm", {
                "history_len": len(staged_history),
                "context_pack_hash": pack_hash or None,
            }, turn=turn_epoch)
            # Voice always wins — if mind holds the gate, force-cancel it
            gate_acquired = False
            for _attempt in range(3):
                try:
                    await asyncio.wait_for(llm_gate.acquire(), timeout=2.0)
                    gate_acquired = True
                    break
                except asyncio.TimeoutError:
                    mind_interrupt.set()
                    # Force-cancel mind_task so its async-with releases the lock
                    if mind_task and not mind_task.done():
                        mind_task.cancel()
                        try:
                            await asyncio.wait_for(asyncio.shield(mind_task), timeout=0.3)
                        except Exception:
                            pass
                        # Restart mind loop after voice finishes
                        mind_task = asyncio.create_task(mind_loop())
                    await _pipeline_log(websocket, "sys",
                        "⚡ Forcing mind yield for voice…", append=False)
                    await asyncio.sleep(0.1)

            if not gate_acquired:
                await _pipeline_log(websocket, "sys",
                    "⚠️ Could not acquire LLM — skipping generation", append=False)
                await _set_state("listening")
                return

            try:
                # Defensive clear: if a stale interrupt_hint arrived between
                # handle_utterance's clear (L356) and now, don't let it kill
                # this fresh generation.
                interrupt_event.clear()
                async for event in llm_stream(staged_history, interrupt=interrupt_event):
                    if interrupt_event.is_set():
                        break

                    if event["type"] == "token":
                        if first_token:
                            ttft = int((time.time() - llm_t0) * 1000)
                            await _pipeline_log(websocket, "llm", f"[TTFT {ttft}ms] ", append=False)
                            first_token = False
                        # Send raw token to browser (frontend renders markdown)
                        raw_text = event["text"]
                        await _safe_send_json(websocket, {"type": "token", "text": raw_text})
                        assistant_text += raw_text
                        # Console gets the cleaned version for readability
                        await _pipeline_log(
                            websocket,
                            "llm",
                            _clean_for_voice(raw_text),
                            append=llm_line_open,
                        )
                        llm_line_open = True
                        slog.log("llm_token", "llm", {"token": raw_text}, turn=turn_epoch)

                    elif event["type"] == "think_token":
                        # Thinking content: shown in UI via think bubbles
                        preview = event["text"][:120].replace("\n", " ") if event["text"] else ""
                        await _pipeline_log(websocket, "llm", f"💭 {preview}…", append=False)
                        await _safe_send_json(websocket, {"type": "think_token", "text": event["text"]})
                        llm_line_open = False   # next real token starts a fresh log line
                        slog.log("llm_think_token", "llm", {"text": event["text"]}, turn=turn_epoch)

                    elif event["type"] == "sentence":
                        sentence = _clean_for_voice(event["text"])
                        if not sentence:
                            continue
                        sp = sentence if len(sentence) <= 160 else sentence[:157] + "…"
                        await _set_state("speaking")
                        is_speaking = True
                        supervisor.set_speaking()  # v5: sync supervisor
                        is_speaking_since = time.monotonic()  # v3.2: timestamp for failsafe
                        await _pipeline_log(
                            websocket,
                            "tts",
                            f"Synthesizing: «{sp}»",
                        )
                        tts_t0 = time.time()
                        slog.log("tts_start", "tts", {
                            "sentence": sentence, "char_count": len(sentence),
                        }, turn=turn_epoch)
                        wav_bytes = await tts.synthesize(sentence)
                        tts_ms = int((time.time() - tts_t0) * 1000)
                        if wav_bytes and not interrupt_event.is_set():
                            await _pipeline_log(
                                websocket,
                                "tts",
                                f"Audio ready ({tts_ms}ms, {len(wav_bytes)//1024}KB) → browser",
                            )
                            await _safe_send_bytes(websocket, wav_bytes)
                            slog.log("tts_done", "tts", {
                                "tts_ms": tts_ms,
                                "wav_kb": len(wav_bytes) // 1024,
                            }, turn=turn_epoch)
                            # Re-broadcast health after TTS lazy-loads so badge updates
                            if tts.did_just_load():
                                await broadcast_health()

                    elif event["type"] == "error":
                        await _pipeline_log(websocket, "llm", f"Error: {event['message']}")
                        await _safe_send_json(websocket, {"type": "error", "message": event["message"]})

                    elif event["type"] == "done":
                        if llm_line_open:
                            total_ms = int((time.time() - llm_t0) * 1000)
                            _log_append("websocket", f"LLM: ── done ({total_ms}ms total)")
                            await _pipeline_log(
                                websocket,
                                "llm",
                                f"\n── done ({total_ms}ms total)",
                                append=True,
                            )
                        if assistant_text:
                            history.append({"role": "assistant", "content": assistant_text})
                            history = _trim_history(history)
                            _log_append("conversation", f"GARY: {assistant_text}")
                            # ── Session log: LLM done + condensed reply ──
                            total_ms = int((time.time() - llm_t0) * 1000)
                            slog.log("llm_done", "llm", {
                                "total_ms": total_ms,
                                "full_text": assistant_text,
                                "token_count": len(assistant_text.split()),
                            }, turn=turn_epoch)
                            slog.log_condensed("gary", "replied", assistant_text, {
                                "total_ms": total_ms,
                            }, turn=turn_epoch)
                        # Let tts_finished handle unlocking is_speaking from browser
                        await _set_state("listening")
                        supervisor.on_tts_finished() # v5: sync supervisor
            finally:
                llm_gate.release()

        current_task = asyncio.create_task(_generate_and_speak())
        supervisor.start_foreground(current_task)  # v5: floor -> FOREGROUND_THINKING

    # ── VAD stack ──────────────────────────────────────────────────────────────
    utterance_queue: asyncio.Queue   = asyncio.Queue()
    supervisor._utterance_queue = utterance_queue  # v5: wire real queue into supervisor
    rolling   = RollingBuffer(capacity_sec=5.0)   # 5-second pre-roll ring
    detector  = SpeechDetector()                   # spectral speech probability

    def _on_utterance(audio_np: np.ndarray):
        asyncio.get_event_loop().call_soon_threadsafe(
            utterance_queue.put_nowait, audio_np
        )

    vad = VADAccumulator(on_utterance=_on_utterance, rolling=rolling, silence_hang_sec=0.55)

    # ── Barge-in state ─────────────────────────────────────────────────────────
    bargein_count = 0   # consecutive high-probability speech frames

    async def _voice_bargein(prob: float):
        """Auto-interrupt any active pipeline task when user speaks up (v3.1)."""
        nonlocal bargein_count, turn_epoch, is_speaking
        active = current_task and not current_task.done()
        if not active:
            return
        # Preempt: cancel agent work, send stop_audio with epoch
        interrupt_event.set()
        turn_epoch += 1
        supervisor.turn_epoch = turn_epoch  # v5: sync supervisor
        await _safe_send_json(websocket, {"type": "stop_audio", "epoch": turn_epoch})
        if current_task and not current_task.done():
            current_task.cancel()
        # v3.1: preserve rolling pre-buffer so first syllable of interrupting
        # speech is not lost. Only the accumulated (echo) buffer is cleared.
        vad.preempt_for_user()
        is_speaking = False
        supervisor._touch_human_activity()  # v5: user is speaking
        supervisor.on_vad_speech_start()    # v5: floor -> USER_SPEAKING
        bargein_count = 0
        await _pipeline_log(websocket, "sys", f"\U0001f399 Barge-in (prob={prob:.2f}) \u00b7 interrupted")
        slog.log("barge_in", "sys", {"prob": round(prob, 3), "epoch": turn_epoch}, turn=turn_epoch)
        slog.log_condensed("system", "barge_in", f"User interrupted (prob={prob:.2f})", {
            "epoch": turn_epoch,
        }, turn=turn_epoch)
        await _set_state("listening")

    # ── Pipeline task ──────────────────────────────────────────────────────────
    async def pipeline_worker():
        """Drain utterance queue and run handle_utterance sequentially."""
        while True:
            item = await utterance_queue.get()
            if isinstance(item, str):
                await handle_utterance(text_override=item)
            else:
                await handle_utterance(audio_np=item)

    worker_task = asyncio.create_task(pipeline_worker())

    # ── v5: Initiative Worker — dedicated queue, not through handle_utterance ──
    async def initiative_worker():
        """Process mind initiative candidates with floor sovereignty checks."""
        nonlocal is_speaking, is_speaking_since
        while True:
            text = await initiative_queue.get()
            # Re-check eligibility — floor may have changed since queued
            if not supervisor.background_eligible:
                log.info("Initiative dropped — floor no longer idle (%s)",
                         supervisor.floor.value)
                continue
            # Speak the initiative through its own audio envelope
            supervisor._transition(FloorState.AGENT_SPEAKING, reason="initiative")
            try:
                is_speaking = True
                supervisor.set_speaking()  # v5: sync supervisor
                is_speaking_since = time.monotonic()
                await _safe_send_json(websocket, {"type": "audio_start", "epoch": turn_epoch})
                wav_bytes = await tts.synthesize(text)
                if wav_bytes and not interrupt_event.is_set():
                    await _safe_send_bytes(websocket, wav_bytes)
                await _set_state("listening")
            except Exception as exc:
                log.warning("Initiative speak failed: %s", exc)
            finally:
                is_speaking = False
                supervisor.on_tts_finished()

    initiative_task = asyncio.create_task(initiative_worker())

    # ── Mind Daemon — pulse-based structured cognition ──────────────────────────
    def _mind_remote_enabled() -> bool:
        return os.getenv("GARY_MIND_REMOTE", "").strip().lower() in (
            "1", "true", "yes", "on",
        )

    async def _remote_mind_pulse(
        phase: str,
        thought_history: list[str],
        avoid_topics: list[str],
        affect_summary_str: str,
        recent_conv: list[str],
    ) -> dict | None:
        """Call mindd sidecar for a pulse. Returns result dict or None on failure."""
        mindd_url = os.getenv("GARY_MINDD_URL", "http://127.0.0.1:7863")
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(5.0, read=30.0)) as client:
                resp = await client.post(
                    f"{mindd_url}/pulse",
                    json={
                        "phase": phase,
                        "recent_thoughts": thought_history[-5:],
                        "avoid_topics": avoid_topics[:10],
                        "affect_summary": affect_summary_str,
                        "open_loops": [],
                        "recent_conversation": recent_conv,
                    },
                )
                if resp.status_code != 200:
                    log.warning("mindd HTTP %d", resp.status_code)
                    return None
                data = resp.json()
                if data.get("error"):
                    log.warning("mindd error: %s", data["error"])
                    return None
                return data
        except Exception as exc:
            log.warning("mindd unreachable: %s", exc)
            return None

    async def _remote_mind_pulse_raced(
        phase: str,
        thought_history: list[str],
        avoid_topics: list[str],
        affect_summary_str: str,
        recent_conv: list[str],
    ) -> dict | None:
        """Run remote mind HTTP concurrently with mind_interrupt (user voice)."""
        if mind_interrupt.is_set():
            mind_interrupt.clear()
            return None
        pulse_task = asyncio.create_task(
            _remote_mind_pulse(
                phase,
                thought_history,
                avoid_topics,
                affect_summary_str,
                recent_conv,
            )
        )
        intr_task = asyncio.create_task(mind_interrupt.wait())
        done, pending = await asyncio.wait(
            {pulse_task, intr_task},
            return_when=asyncio.FIRST_COMPLETED,
        )
        for t in pending:
            t.cancel()
        for t in pending:
            try:
                await t
            except asyncio.CancelledError:
                pass

        if intr_task in done:
            if not pulse_task.done():
                pulse_task.cancel()
            try:
                await pulse_task
            except asyncio.CancelledError:
                pass
            mind_interrupt.clear()
            await _pipeline_log(
                websocket,
                "mind",
                "Yielded · voice preempted background pulse",
            )
            return None

        try:
            intr_task.cancel()
            await intr_task
        except asyncio.CancelledError:
            pass

        if pulse_task.cancelled():
            return None
        try:
            return pulse_task.result()
        except Exception as exc:
            log.warning("remote mind pulse failed: %s", exc)
            return None

    async def supervisor_tick_loop():
        """Advances the floor state machine and orphaned turn check regardless of mind sleeps."""
        while True:
            await asyncio.sleep(1)
            supervisor.tick()
            supervisor.check_orphaned_turn()

    tick_task = asyncio.create_task(supervisor_tick_loop())

    mind_llm_share_warned = False

    async def mind_loop():
        """Background thought generation.

        When GARY_MIND_REMOTE=1: calls mindd sidecar over HTTP (no llm_gate).
        Otherwise: embedded mode using llm_stream through llm_gate (legacy).
        """
        nonlocal mind_llm_share_warned
        deduplicator = ThoughtDeduplicator()
        rate_limiter = InitiativeRateLimiter()
        rumination_gov = RuminationGovernor()
        thought_history: list[str] = []

        while True:
            try:
                # Wait a base interval before checking
                await asyncio.sleep(5)

                # v5: The Attention Kernel gate — ONLY authority for background mind
                if not supervisor.background_eligible:
                    continue

                # Wait for LLM to be healthy before attempting thoughts
                if not _mind_remote_enabled() and not watchdog.is_healthy:
                    await _pipeline_log(
                        websocket, "mind",
                        f"⏳ Waiting for LLM (watchdog: {watchdog.state.value})…",
                    )
                    await asyncio.sleep(10)
                    continue

                # Check if paused due to stale thoughts
                if deduplicator.is_paused():
                    remaining = deduplicator.pause_remaining()
                    await asyncio.sleep(min(remaining, 30))
                    continue

                # v4: Check rumination governor
                if rumination_gov.is_in_cooldown:
                    await _pipeline_log(websocket, "mind", "Governor cooldown active")
                    await asyncio.sleep(10)
                    continue

                idle_secs = time.monotonic() - last_activity
                affect.decay()  # update emotional state

                phase = select_phase(
                    idle_secs,
                    curiosity=affect.curiosity,
                    excitement=affect.excitement,
                    anxiety=affect.anxiety,
                    mental_load=affect.mental_load,
                )
                if phase is None:
                    continue

                # v4: Apply rumination governor (may force phase or inject directive)
                rum_state = rumination_gov.check()
                if rum_state.forced_phase:
                    phase = rum_state.forced_phase
                    await _pipeline_log(
                        websocket, "mind",
                        f"Governor forced phase → {phase}",
                    )
                if phase is None:
                    continue

                # ── Shared pre-pulse setup ────────────────────────────────────
                affect_summary = format_affect_summary(affect.to_dict())
                avoid_topics = deduplicator.get_recent_topics(thought_history)
                recent_conv = [
                    f"{h['role']}: {h['content'][:100]}" for h in history[-6:]
                ]
                thought_id = new_thought_id()

                if mind_interrupt.is_set():
                    mind_interrupt.clear()
                    continue

                if _mind_remote_enabled() and not mind_llm_share_warned:
                    from pipeline.llm import LLM_URL

                    mindd_llm = os.getenv(
                        "GARY_MINDD_LLM_URL",
                        "http://localhost:8088/v1/chat/completions",
                    )
                    if _same_llm_origin(LLM_URL, mindd_llm):
                        log.warning(
                            "GARY_MINDD_LLM_URL shares the inference server with voice reflex — "
                            "mind pulses can stall replies; set GARY_MINDD_LLM_URL to a dedicated sidecar."
                        )
                        await _pipeline_log(
                            websocket,
                            "sys",
                            "Mind + voice share one LLM — set GARY_MINDD_LLM_URL to a sidecar for responsive voice",
                        )
                    mind_llm_share_warned = True

                # Notify browser of phase change
                await _safe_send_json(websocket, {
                    "type": "mind_phase",
                    "phase": phase,
                    "label": PHASE_LABELS.get(phase, phase),
                })
                await _pipeline_log(websocket, "mind", f"{PHASE_LABELS.get(phase, phase)}…")

                # ── Remote mode: call mindd sidecar (NO llm_gate) ─────────
                if _mind_remote_enabled():
                    result = await _remote_mind_pulse_raced(
                        phase, thought_history, avoid_topics,
                        affect_summary, recent_conv,
                    )
                    if result is None:
                        continue

                    clean_text = result.get("clean_text", "")
                    salience = result.get("salience", 0.3)
                    thought_id = result.get("thought_id", thought_id)

                    if not clean_text.strip():
                        continue

                    # Dedup check
                    is_dup = deduplicator.is_duplicate(clean_text, thought_history)
                    deduplicator.record_outcome(is_dup)
                    if is_dup:
                        await _pipeline_log(websocket, "mind", "(thought too similar — discarded)")
                        continue

                    # Send complete thought to browser
                    await _safe_send_json(websocket, {
                        "type": "mind_token",
                        "token": clean_text,
                        "thought_id": thought_id,
                        "phase": phase,
                    })
                    slog.log("mind_thought", "mind", {
                        "thought_id": thought_id, "phase": phase,
                        "text": clean_text, "salience": salience,
                        "is_dup": False, "remote": True,
                    }, turn=turn_epoch)
                    slog.log_condensed("mind", "thought", clean_text[:200], {
                        "phase": phase, "salience": round(salience, 2),
                    }, turn=turn_epoch)

                    # Persist if enabled
                    if result.get("pulse") and os.getenv(
                        "GARY_PERSIST_MIND", ""
                    ).strip().lower() in ("1", "true", "yes", "on"):
                        try:
                            from memory.db import get_pool
                            from memory.mind_persist import persist_structured_thought
                            from core.mind_pulse import MindPulse, ThoughtFrame, InitiativeCandidate

                            pulse_data = result["pulse"]
                            pulse_obj = MindPulse(
                                schema_version=pulse_data.get("schema_version", 1),
                                inner_voice=pulse_data.get("inner_voice", []),
                                frames=[
                                    ThoughtFrame(**f) for f in pulse_data.get("frames", [])
                                ],
                                initiative_candidate=(
                                    InitiativeCandidate(**pulse_data["initiative_candidate"])
                                    if pulse_data.get("initiative_candidate")
                                    else None
                                ),
                            )
                            pool = await get_pool()
                            async with pool.acquire() as conn:
                                await persist_structured_thought(
                                    conn,
                                    thought_id=thought_id,
                                    session_id="",
                                    pulse=pulse_obj,
                                    phase=phase,
                                    salience=salience,
                                    may_surface=bool(result.get("initiative")),
                                )
                        except Exception as exc:
                            log.warning("mind persist skipped: %s", exc)

                    # Store in rolling history + rumination tracking
                    thought_history.append(clean_text)
                    rumination_gov.record_thought(clean_text)
                    if len(thought_history) > 10:
                        thought_history.pop(0)

                    # Finalize thought in browser
                    await _safe_send_json(websocket, {
                        "type": "mind_done",
                        "thought_id": thought_id,
                        "salience": round(salience, 2),
                        "phase": phase,
                    })

                    preview = clean_text[:80].replace("\n", " ")
                    await _pipeline_log(
                        websocket, "mind",
                        f"Thought [remote] (s={salience:.2f}): {preview}…",
                    )

                    # Handle initiative
                    init_data = result.get("initiative")
                    if init_data and rate_limiter.can_speak():
                        rate_limiter.record()
                        await _pipeline_log(
                            websocket, "mind",
                            f"🗣️ Initiative triggered: {init_data.get('reason', '?')}",
                        )
                        await _safe_send_json(websocket, {
                            "type": "initiative",
                            "text": init_data["text"],
                            "reason": init_data.get("reason", ""),
                            "thought_ref": thought_id,
                        })
                        slog.log("mind_initiative", "mind", {
                            "text": init_data["text"],
                            "reason": init_data.get("reason", ""),
                            "thought_ref": thought_id,
                        }, turn=turn_epoch)
                        slog.log_condensed("mind", "initiative", init_data["text"], {
                            "reason": init_data.get("reason", ""),
                        }, turn=turn_epoch)
                        initiative_queue.put_nowait(init_data["text"])  # v5: dedicated queue, not utterance_queue

                # ── Embedded mode: use llm_stream through llm_gate (legacy) ──
                else:
                    # Try to acquire LLM — non-blocking, skip if voice is using it
                    if llm_gate.locked():
                        continue

                    async with llm_gate:
                        # Double-check we're still idle
                        if time.monotonic() - last_activity < 10:
                            continue

                        use_mind_json = mind_json_enabled()
                        messages = build_mind_prompt(
                            phase=phase,
                            recent_thoughts=thought_history,
                            avoid_topics=avoid_topics,
                            affect_summary=affect_summary,
                            open_loops=[],
                            recent_conversation=recent_conv,
                            json_mode=use_mind_json,
                        )

                        full_text = ""
                        mind_aborted = False
                        mind_interrupt.clear()
                        async for event in llm_stream(
                            messages,
                            interrupt=mind_interrupt,
                        ):
                            if mind_interrupt.is_set():
                                log.info("Mind stream interrupted by voice")
                                mind_aborted = True
                                break
                            if time.monotonic() - last_activity < 2:
                                log.info("Mind stream yielding to user activity")
                                mind_aborted = True
                                break

                            if event["type"] == "token":
                                full_text += event["text"]
                                await _safe_send_json(websocket, {
                                    "type": "mind_token",
                                    "token": event["text"],
                                    "thought_id": thought_id,
                                    "phase": phase,
                                })
                            elif event["type"] == "think_token":
                                full_text += event["text"]
                                await _safe_send_json(websocket, {
                                    "type": "mind_token",
                                    "token": event["text"],
                                    "thought_id": thought_id,
                                    "phase": phase,
                                })
                            elif event["type"] == "done":
                                break

                        if mind_aborted:
                            mind_interrupt.clear()
                            continue

                        if not full_text.strip():
                            continue

                        # Dedup check
                        is_dup = deduplicator.is_duplicate(full_text, thought_history)
                        deduplicator.record_outcome(is_dup)
                        if is_dup:
                            await _pipeline_log(websocket, "mind", "(thought too similar — discarded)")
                            continue

                        clean_text, initiative, salience, pulse_obj = process_mind_response(
                            full_text,
                            thought_id,
                            phase,
                            json_mode=use_mind_json,
                        )

                        if pulse_obj is not None and os.getenv(
                            "GARY_PERSIST_MIND", ""
                        ).strip().lower() in ("1", "true", "yes", "on"):
                            try:
                                from memory.db import get_pool
                                from memory.mind_persist import persist_structured_thought

                                pool = await get_pool()
                                async with pool.acquire() as conn:
                                    await persist_structured_thought(
                                        conn,
                                        thought_id=thought_id,
                                        session_id="",
                                        pulse=pulse_obj,
                                        phase=phase,
                                        salience=salience,
                                        may_surface=bool(initiative),
                                    )
                            except Exception as exc:
                                log.warning("mind persist skipped: %s", exc)

                        # Store in rolling history + rumination tracking
                        thought_history.append(clean_text)
                        rumination_gov.record_thought(clean_text)
                        if len(thought_history) > 10:
                            thought_history.pop(0)

                        # Finalize thought in browser
                        await _safe_send_json(websocket, {
                            "type": "mind_done",
                            "thought_id": thought_id,
                            "salience": round(salience, 2),
                            "phase": phase,
                        })

                        phase_label = PHASE_LABELS.get(phase, phase).rstrip("…")
                        _log_append("mind_stream", f"{phase_label}: {clean_text}")

                        preview = clean_text[:80].replace("\n", " ")
                        await _pipeline_log(
                            websocket, "mind",
                            f"Thought (s={salience:.2f}): {preview}…",
                        )

                        # Handle initiative (bridge to voice pipeline)
                        if initiative and rate_limiter.can_speak():
                            rate_limiter.record()
                            await _pipeline_log(
                                websocket, "mind",
                                f"🗣️ Initiative triggered: {initiative.reason}",
                            )
                            await _safe_send_json(websocket, {
                                "type": "initiative",
                                "text": initiative.text,
                                "reason": initiative.reason,
                                "thought_ref": thought_id,
                            })
                            initiative_queue.put_nowait(initiative.text)  # v5: dedicated queue, not utterance_queue

                # Cooldown before next pulse
                cooldown = PHASE_COOLDOWNS.get(phase, 60)
                await asyncio.sleep(cooldown)

            except asyncio.CancelledError:
                log.info("Mind loop cancelled")
                return
            except Exception as exc:
                log.exception(f"Mind loop error: {exc}")
                await asyncio.sleep(30)  # back off on error

    mind_task = asyncio.create_task(mind_loop())

    # ── Greet (pre-baked WAV — instant, no TTS wait) ────────────────────────────
    await _set_state("listening")  # Keeps frontend onset detector asleep to avoid echo barge-in
    is_speaking = True             # Keeps backend VAD asleep to avoid echo barge-in
    supervisor.set_speaking()       # v5: sync supervisor
    is_speaking_since = time.monotonic()  # v3.2: timestamp for failsafe

    greeting = "Hello! I'm GARY, your personal AI. I'm listening — just start talking."
    await _safe_send_json(websocket, {
        "type": "greeting",
        "text": greeting,
    })

    # Serve from disk if available; lazy-generate on first ever boot
    greeting_wav_path = STATIC_DIR / "audio" / "greeting.wav"
    greeting_wav: bytes = b""
    if greeting_wav_path.exists():
        try:
            greeting_wav = greeting_wav_path.read_bytes()
            await _pipeline_log(websocket, "tts", f"Greeting audio: {len(greeting_wav)//1024}KB (pre-baked) → browser")
        except OSError as exc:
            log.warning("Failed to read greeting WAV: %s", exc)

    if not greeting_wav:
        # First boot or file missing — synthesize and cache for next time
        await _pipeline_log(websocket, "tts", "Greeting · synthesizing (first boot)…")
        greeting_wav = await tts.synthesize(greeting)
        if greeting_wav:
            try:
                greeting_wav_path.parent.mkdir(parents=True, exist_ok=True)
                greeting_wav_path.write_bytes(greeting_wav)
                log.info("Cached greeting WAV → %s (%dKB)", greeting_wav_path, len(greeting_wav)//1024)
            except OSError as exc:
                log.warning("Could not cache greeting WAV: %s", exc)
            await _pipeline_log(websocket, "tts", f"Greeting audio: {len(greeting_wav)//1024}KB WAV → browser")

    if greeting_wav:
        await _safe_send_json(websocket, {"type": "audio_start", "epoch": turn_epoch})
        await _safe_send_bytes(websocket, greeting_wav)
        slog.log("greeting", "sys", {
            "text": greeting, "wav_kb": len(greeting_wav) // 1024,
        })
        slog.log_condensed("gary", "greeted", greeting, {
            "wav_kb": len(greeting_wav) // 1024,
        })

    # ── Message receive loop ───────────────────────────────────────────────────
    try:
        while True:
            msg = await websocket.receive()

            if "bytes" in msg and msg["bytes"]:
                # Audio chunk: Float32 PCM
                data = msg["bytes"]
                if len(data) >= 4:
                    chunk = np.frombuffer(data, dtype=np.float32)

                    # ── Speech probability (spectral + RMS, ~0.2ms) ──────────
                    prob = detector.probability(chunk)

                    # ── Stream VAD probability to browser (real-time meter) ───
                    await _safe_send_json(websocket, {"type": "vad", "prob": prob})

                    # ── Rolling pre-roll: always update regardless of state ───
                    rolling.push(chunk)

                    # ── Tripwire barge-in: fires during THINKING or SPEAKING ──
                    # Any active current_task (LLM streaming or TTS synthesis)
                    is_active = current_task is not None and not current_task.done()
                    # v3.2: failsafe — if is_speaking stuck True for too long, force-reset
                    if is_speaking and (time.monotonic() - is_speaking_since) > IS_SPEAKING_TIMEOUT:
                        is_speaking = False
                        log.warning("is_speaking failsafe: forced reset after %.0fs", IS_SPEAKING_TIMEOUT)
                    if is_active:
                        if prob >= BARGEIN_PROB:
                            bargein_count += 1
                            if bargein_count >= BARGEIN_FRAMES:
                                await _voice_bargein(prob)
                        else:
                            bargein_count = max(0, bargein_count - 1)
                    else:
                        bargein_count = 0

                    # v3.1: guard against echo — don't accumulate VAD during
                    # agent speech (the rolling pre-buffer at L1012 still runs)
                    if not (is_speaking or is_active):
                        was_in_speech = vad.state != "idle"
                        vad.push(chunk, prob=prob)
                        is_in_speech = vad.state != "idle"

                        # v5: Sync supervisor with normal idle-speech boundaries
                        if not was_in_speech and is_in_speech:
                            supervisor.on_vad_speech_start()
                        elif was_in_speech and not is_in_speech:
                            supervisor.on_vad_speech_end()

            elif "text" in msg and msg["text"]:
                try:
                    cmd = json.loads(msg["text"])
                except json.JSONDecodeError:
                    continue

                if cmd.get("type") == "tts_finished":
                    is_speaking = False
                    supervisor.on_tts_finished()  # v5: starts cooldown
                    await _set_state("listening")
                    continue

                if cmd.get("type") in ("interrupt", "interrupt_hint"):
                    supervisor.on_onset()  # v5: immediately lock floor (USER_ACQUIRING)
                    # Capture the epoch BEFORE incrementing — this interrupt
                    # is for the generation that was active at this epoch.
                    stale_epoch = turn_epoch
                    interrupt_event.set()
                    turn_epoch += 1
                    supervisor.turn_epoch = turn_epoch  # v5: sync supervisor
                    supervisor._touch_human_activity()  # v5: user is acting
                    await _safe_send_json(websocket, {"type": "stop_audio", "epoch": turn_epoch})
                    _label = "Interrupt hint" if cmd.get("type") == "interrupt_hint" else "Interrupt"
                    _extra = ""
                    if cmd.get("type") == "interrupt_hint":
                        cpm = cmd.get("client_perf_ms")
                        if cpm is not None:
                            _extra = f" (client_perf_ms={cpm})"
                    await _pipeline_log(
                        websocket, "sys",
                        f"{_label} · cancelled generation / cleared VAD buffer{_extra}",
                    )
                    slog.log("interrupt", "user", {
                        "type": cmd.get("type"), "epoch": turn_epoch,
                    }, turn=turn_epoch)
                    slog.log_condensed("user", "interrupted", _label, {
                        "epoch": turn_epoch,
                    }, turn=turn_epoch)
                    # Only cancel current_task if it belongs to the stale epoch.
                    # handle_utterance() increments turn_epoch BEFORE creating the
                    # new task, so if turn_epoch has already moved past stale_epoch
                    # the new task is safe.
                    if current_task and not current_task.done():
                        current_task.cancel()
                    # v3.1: preserve rolling pre-buffer for onset capture
                    vad.preempt_for_user()
                    is_speaking = False
                    supervisor.is_speaking_since = 0.0  # v5: sync supervisor
                    bargein_count = 0
                    await _set_state("listening")

                elif cmd.get("type") == "stop":
                    vad.finalize()

                elif cmd.get("type") == "clear":
                    history.clear()
                    await _pipeline_log(websocket, "sys", "Chat history cleared")
                    await _safe_send_json(websocket, {"type": "cleared"})

                elif cmd.get("type") == "set_voice":
                    voice = cmd.get("voice", "")
                    ok = tts.set_voice(voice)
                    await _safe_send_json(websocket, {
                        "type":    "voice_changed",
                        "voice":   tts.get_voice(),
                        "success": ok,
                    })
                    await _pipeline_log(
                        websocket, "tts",
                        f"Voice → {tts.get_voice()}" + ("" if ok else f" ('{voice}' not found)"),
                    )

    except WebSocketDisconnect:
        log.info(f"[gary] disconnected: {websocket.client}")
    except Exception as exc:
        log.exception(f"[gary] error: {exc}")
    finally:
        _sess_dur = round(time.monotonic() - _sess_t0, 2)
        slog.log("session_end", "sys", {
            "duration_sec": _sess_dur, "turn_count": turn_epoch,
        })
        slog.log_condensed("system", "disconnected", f"Session ended after {_sess_dur}s", {
            "duration_sec": _sess_dur, "turn_count": turn_epoch,
        })
        await slog.close()
        mind_task.cancel()
        worker_task.cancel()
        if current_task and not current_task.done():
            current_task.cancel()


# ── Entry point ────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import ssl as _ssl

    ssl_certfile = os.getenv("SSL_CERTFILE")
    ssl_keyfile  = os.getenv("SSL_KEYFILE")

    uvicorn.run(
        "server:app",
        host="0.0.0.0",
        port=PORT,
        log_level=LOG_LEVEL.lower(),
        ssl_certfile=ssl_certfile,
        ssl_keyfile=ssl_keyfile,
        reload=False,
        workers=1,
    )
