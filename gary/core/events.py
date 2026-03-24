"""
core/events.py — Typed event models for the GARY event bus

Every event that flows through the system (utterance, response, thought,
affect change, tool call) is represented as a typed Pydantic model.

Events are:
  1. Emitted by the Reflex Core (fire-and-forget to async outbox)
  2. Written to the `events` table (append-only, partitioned)
  3. Consumed by the Mind Daemon via LISTEN/NOTIFY

Design: these are lightweight value objects. No DB logic here.
"""
from __future__ import annotations

import time
import uuid
from enum import Enum
from typing import Any, Dict, Optional

from pydantic import BaseModel, ConfigDict, Field


class Actor(str, Enum):
    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"
    DREAMER = "dreamer"
    VALIDATOR = "validator"


class EventKind(str, Enum):
    UTTERANCE = "utterance"
    RESPONSE = "response"
    THOUGHT = "thought"
    AFFECT = "affect"
    IDEA = "idea"
    TOOL = "tool"
    ERROR = "error"
    SESSION = "session"    # connect, disconnect


class EpistemicStatus(str, Enum):
    OBSERVED = "observed"
    INFERRED = "inferred"
    IMAGINED = "imagined"
    VALIDATED = "validated"
    SUPERSEDED = "superseded"


class GaryEvent(BaseModel):
    """Base event that flows through the system."""
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    ts: float = Field(default_factory=time.time)
    session_id: str = ""
    actor: Actor = Actor.SYSTEM
    kind: EventKind = EventKind.SESSION
    payload: Dict[str, Any] = Field(default_factory=dict)
    artifact_id: Optional[str] = None
    parent_id: Optional[str] = None
    epistemic_status: EpistemicStatus = EpistemicStatus.OBSERVED

    model_config = ConfigDict(use_enum_values=True)


class UtteranceEvent(GaryEvent):
    """User said something (after ASR)."""
    kind: EventKind = EventKind.UTTERANCE
    actor: Actor = Actor.USER

    @classmethod
    def from_transcript(cls, session_id: str, text: str, audio_duration_sec: float = 0.0,
                        asr_ms: int = 0) -> "UtteranceEvent":
        return cls(
            session_id=session_id,
            payload={
                "text": text,
                "audio_duration_sec": round(audio_duration_sec, 2),
                "asr_ms": asr_ms,
            },
        )


class ResponseEvent(GaryEvent):
    """GARY responded (after LLM + TTS)."""
    kind: EventKind = EventKind.RESPONSE
    actor: Actor = Actor.ASSISTANT

    @classmethod
    def from_response(cls, session_id: str, text: str, ttft_ms: int = 0,
                      total_ms: int = 0, parent_id: str = "") -> "ResponseEvent":
        return cls(
            session_id=session_id,
            parent_id=parent_id or None,
            payload={
                "text": text,
                "ttft_ms": ttft_ms,
                "total_ms": total_ms,
            },
        )


class AffectEvent(GaryEvent):
    """Affect state snapshot (sparse persistence)."""
    kind: EventKind = EventKind.AFFECT
    actor: Actor = Actor.SYSTEM

    @classmethod
    def from_vector(cls, session_id: str, affect_dict: Dict[str, float],
                    trigger: str = "") -> "AffectEvent":
        return cls(
            session_id=session_id,
            payload={
                "affect": affect_dict,
                "trigger": trigger,
            },
        )


class ThoughtEvent(GaryEvent):
    """A structured inner dialogue thought."""
    kind: EventKind = EventKind.THOUGHT
    actor: Actor = Actor.ASSISTANT

    @classmethod
    def from_thought(cls, session_id: str, lane: str, text: str,
                     salience: float = 0.5, may_surface: bool = False,
                     emotional_color: str = "neutral",
                     epistemic_status: EpistemicStatus = EpistemicStatus.INFERRED) -> "ThoughtEvent":
        return cls(
            session_id=session_id,
            epistemic_status=epistemic_status,
            payload={
                "lane": lane,
                "text": text,
                "salience": salience,
                "may_surface": may_surface,
                "emotional_color": emotional_color,
            },
        )
