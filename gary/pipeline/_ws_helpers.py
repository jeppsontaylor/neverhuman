"""
pipeline/_ws_helpers.py — WebSocket send helpers (extracted to avoid circular imports)

Used by both server.py and turn_supervisor.py.
"""

import json
import logging
from starlette.websockets import WebSocketState

log = logging.getLogger("gary.ws")


async def safe_send_json(ws, payload: dict) -> None:
    """Send JSON to a WebSocket, silently swallowing errors."""
    try:
        if ws.client_state == WebSocketState.CONNECTED:
            await ws.send_text(json.dumps(payload))
    except Exception:
        pass


async def safe_send_bytes(ws, data: bytes) -> None:
    """Send binary data to a WebSocket, silently swallowing errors."""
    try:
        if ws.client_state == WebSocketState.CONNECTED:
            await ws.send_bytes(data)
    except Exception:
        pass
