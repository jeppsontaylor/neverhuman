"""
pipeline/turn_supervisor.py — Per-session turn arbitrator (v6)

The Attention Kernel.  Owns the canonical truth of floor ownership,
engagement state, system mode, turn resolution, and background leases.

v6 replaces the v5 flat FloorState enum with three orthogonal axes
(FloorOwner × Engagement × SystemMode) and an event-driven `apply()`
method that validates all transitions atomically.  Direct field mutation
is forbidden from outside this module.

Orthogonal axes:
    FloorOwner   — who holds the conversational floor right now
    Engagement   — how "warm" the conversation is
    SystemMode   — normal / maintenance / safe_mode

Derived predicates:
    background_eligible  — silent background work (no speech, no initiative)
    surface_eligible     — initiative may speak

Background & initiative tasks must acquire a BackgroundLease before doing
any work.  Leases are auto-revoked on every state transition.

Usage (in server.py, per WebSocket connection):
    supervisor = TurnSupervisor(utterance_queue)

    supervisor.apply(Event.MIC_ONSET)
    supervisor.apply(Event.VAD_SPEECH_START)
    supervisor.apply(Event.VAD_SPEECH_END)
    supervisor.apply(Event.TRANSCRIPT_READY, turn_id=uuid4())
    supervisor.apply(Event.EMPTY_TRANSCRIPT)
    supervisor.apply(Event.FOREGROUND_GEN_START, task=current_task)
    supervisor.apply(Event.FIRST_AUDIO_SENT)
    supervisor.apply(Event.TTS_FINISHED)
    supervisor.apply(Event.BARGE_IN, reason="worklet_onset")
    supervisor.apply(Event.MAINTENANCE_BEGIN, job_id=uuid4())
    supervisor.apply(Event.MAINTENANCE_END, job_id=uuid4())
    supervisor.apply(Event.MARK_READY)

    lease = supervisor.try_acquire_lease("mind_pulse", ttl=5.0)
    if lease and lease.valid: ...

    epoch = await supervisor.preempt(ws, reason="bargein")

Backward compatibility:
    The old FloorState enum is preserved as a derived alias so that
    callers that only read `supervisor.floor` still work.  The old
    named methods (on_onset, on_vad_speech_start, etc.) are preserved
    as thin wrappers around `apply()`.
"""

import asyncio
import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Literal, Optional
from uuid import UUID, uuid4

from pipeline.turn_classifier import TurnMode

log = logging.getLogger("gary.turn")


# ─── Orthogonal state axes ───────────────────────────────────────────────────

class FloorOwner(str, Enum):
    BOOT      = "boot"
    USER      = "user"          # onset → speech → ASR
    ASR       = "asr"           # transcript arrived, not yet answered
    ASSISTANT = "assistant"     # thinking or speaking
    NONE      = "none"          # no one owns the floor


class Engagement(str, Enum):
    HOT      = "hot"            # active exchange
    COOLDOWN = "cooldown"       # post-turn, conversation warm
    ENGAGED  = "engaged"        # user likely present, silence
    IDLE     = "idle"           # truly idle


class SystemMode(str, Enum):
    NORMAL      = "normal"
    MAINTENANCE = "maintenance"  # self-edit in progress
    SAFE_MODE   = "safe_mode"    # degraded (invariant breaches)


# ─── Events ──────────────────────────────────────────────────────────────────

class Event(str, Enum):
    MARK_READY         = "mark_ready"
    MIC_ONSET          = "mic_onset"
    MIC_ONSET_END      = "mic_onset_end"
    VAD_SPEECH_START   = "vad_speech_start"
    VAD_SPEECH_END     = "vad_speech_end"
    TRANSCRIPT_READY   = "transcript_ready"
    EMPTY_TRANSCRIPT   = "empty_transcript"
    FOREGROUND_GEN_START = "foreground_gen_start"
    FIRST_AUDIO_SENT   = "first_audio_sent"
    TTS_FINISHED       = "tts_finished"
    END_TURN           = "end_turn"
    BARGE_IN           = "barge_in"
    MAINTENANCE_BEGIN  = "maintenance_begin"
    MAINTENANCE_END    = "maintenance_end"


# ─── Turn resolution ─────────────────────────────────────────────────────────

class TurnStatus(str, Enum):
    PENDING    = "pending"       # transcript landed, no answer yet
    ANSWERING  = "answering"     # LLM generating / TTS playing
    ANSWERED   = "answered"      # response delivered
    SUPERSEDED = "superseded"    # user asked something new
    ERROR      = "error"         # model/TTS failed
    TIMEOUT    = "timeout"       # orphan watchdog fired
    ABANDONED  = "abandoned"     # user disconnected


@dataclass
class TurnRecord:
    turn_id: UUID
    transcript_at: float = field(default_factory=time.monotonic)
    status: TurnStatus = TurnStatus.PENDING
    answer_started_at: Optional[float] = None
    answer_ended_at: Optional[float] = None
    superseded_by: Optional[UUID] = None
    failure_reason: Optional[str] = None
    turn_group_id: Optional[UUID] = None  # groups segmented long speech


# ─── Revocable leases ────────────────────────────────────────────────────────

@dataclass
class BackgroundLease:
    id: UUID
    kind: str             # "mind_pulse" | "initiative_speak"
    granted_at: float
    floor_revision: int
    ttl: float            # max 5s for mind, 10s for initiative
    revoked: bool = False
    _supervisor: "TurnSupervisor | None" = field(default=None, repr=False)

    @property
    def valid(self) -> bool:
        if self.revoked:
            return False
        if (time.monotonic() - self.granted_at) > self.ttl:
            return False
        if self._supervisor is not None and self._supervisor.floor_revision != self.floor_revision:
            return False
        return True


# ─── Legacy FloorState (derived, backward-compatible) ────────────────────────

class FloorState(str, Enum):
    """Backward-compatible derived state for UI/logging.

    Mapped from the orthogonal axes.  Never set directly.
    """
    BOOTING            = "booting"
    USER_ACQUIRING     = "user_acquiring"
    USER_SPEAKING      = "user_speaking"
    ASR_PENDING        = "asr_pending"
    FOREGROUND_THINKING = "foreground_thinking"
    AGENT_SPEAKING     = "agent_speaking"
    COOLDOWN           = "cooldown"
    ENGAGED_SILENCE    = "engaged_silence"
    TRULY_IDLE         = "truly_idle"
    MAINTENANCE        = "maintenance"


# ─── Presence & social budget ─────────────────────────────────────────────────

@dataclass
class PresenceEstimate:
    confidence: float = 0.3    # 0=absent, 1=definitely present
    last_evidence: float = 0.0
    source: str = "none"       # ws_healthy | recent_activity | tab_visible


@dataclass
class SocialBudget:
    max_per_hour: int = 3
    attempts: list = field(default_factory=list)
    accepted: int = 0
    ignored: int = 0

    @property
    def remaining(self) -> int:
        now = time.monotonic()
        recent = [t for t in self.attempts if now - t < 3600]
        return max(0, self.max_per_hour - len(recent))

    def record_attempt(self) -> None:
        self.attempts.append(time.monotonic())

    def record_accepted(self) -> None:
        self.accepted += 1

    def record_ignored(self) -> None:
        self.ignored += 1


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  TurnSupervisor — the Attention Kernel
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class TurnSupervisor:
    """Per-session turn arbitrator.  Owns floor, engagement, mode, leases, and cancellation."""

    # ── Timing constants (seconds) ────────────────────────────────────────────
    COOLDOWN_SECONDS         = 8.0    # post-turn warmth before engaged_silence
    ENGAGED_SILENCE_SECONDS  = 60.0   # silence before truly_idle
    MIN_IDLE_FOR_THOUGHT     = 5.0    # min seconds in ENGAGED/IDLE before mind may pulse
    ORPHAN_TIMEOUT_SECONDS   = 60.0   # force-clear pending turn after this
    IS_SPEAKING_TIMEOUT      = 30.0   # hard cap on speaking duration
    SAFE_MODE_THRESHOLD      = 3      # floor violations before safe_mode

    def __init__(self, utterance_queue: asyncio.Queue):
        # ── Orthogonal state axes ──────────────────────────────────────────────
        self.floor_owner: FloorOwner = FloorOwner.BOOT
        self.engagement: Engagement = Engagement.IDLE
        self.mode: SystemMode = SystemMode.NORMAL

        # ── Floor revision (monotonic, incremented on every state change) ─────
        self.floor_revision: int = 0

        # ── Legacy epoch (incremented only on preemption) ─────────────────────
        self.turn_epoch: int = 0

        # ── Turn mode classification ──────────────────────────────────────────
        self.turn_mode: TurnMode = TurnMode.LAYERED

        # ── Turn tracking ─────────────────────────────────────────────────────
        self._current_turn: Optional[TurnRecord] = None
        self._turn_history: list[TurnRecord] = []
        self.active_turn_task: asyncio.Task | None = None
        self.filler_task: asyncio.Task | None = None

        # ── Activity timestamps (monotonic) ───────────────────────────────────
        self.last_human_activity: float = time.monotonic()
        self.last_attention_release: float = time.monotonic()
        self.last_agent_audio_end: float = 0.0
        self.is_speaking_since: float = 0.0

        # ── Sensor state ──────────────────────────────────────────────────────
        self.vad_hot: bool = False
        self.mic_onset_active: bool = False
        self.active_asr_job: bool = False

        # ── Conversation heat (decaying float) ────────────────────────────────
        self.conversation_heat: float = 0.0
        self._heat_set_time: float = 0.0

        # ── Presence & social ─────────────────────────────────────────────────
        self.presence: PresenceEstimate = PresenceEstimate()
        self.social_budget: SocialBudget = SocialBudget()

        # ── Leases ────────────────────────────────────────────────────────────
        self._active_leases: list[BackgroundLease] = []

        # ── Metrics ───────────────────────────────────────────────────────────
        self.orphaned_turn_count: int = 0
        self.floor_violation_count: int = 0

        # ── Internal ──────────────────────────────────────────────────────────
        self._utterance_queue = utterance_queue

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    #  Event-driven transitions — the core API
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    def apply(self, event: Event, **kwargs) -> None:
        """Apply an event to the floor machine.

        This is the ONLY way to change state.  It:
          - validates the transition
          - updates all coupled fields atomically
          - increments floor_revision
          - revokes all outstanding leases
          - emits a structured log entry
        """
        old_owner = self.floor_owner
        old_engagement = self.engagement
        old_mode = self.mode

        # ── Dispatch ──────────────────────────────────────────────────────────
        if event == Event.MARK_READY:
            self.floor_owner = FloorOwner.NONE
            self.engagement = Engagement.IDLE

        elif event == Event.MIC_ONSET:
            self._touch_human_activity()
            self.mic_onset_active = True
            if self.floor_owner in (FloorOwner.NONE,):
                self.floor_owner = FloorOwner.USER
            self.engagement = Engagement.HOT

        elif event == Event.MIC_ONSET_END:
            self.mic_onset_active = False

        elif event == Event.VAD_SPEECH_START:
            self._touch_human_activity()
            self.vad_hot = True
            self.mic_onset_active = True
            self.floor_owner = FloorOwner.USER
            self.engagement = Engagement.HOT

        elif event == Event.VAD_SPEECH_END:
            self.vad_hot = False
            self.mic_onset_active = False
            # floor_owner stays USER — ASR still processing

        elif event == Event.TRANSCRIPT_READY:
            turn_id = kwargs.get("turn_id")
            if turn_id is None:
                raise ValueError("TRANSCRIPT_READY requires turn_id")
            self._touch_human_activity()
            # Supersede any pending turn
            if self._current_turn and self._current_turn.status == TurnStatus.PENDING:
                self._current_turn.status = TurnStatus.SUPERSEDED
                self._current_turn.superseded_by = turn_id
                self._turn_history.append(self._current_turn)
            self._current_turn = TurnRecord(turn_id=turn_id)
            self.active_asr_job = False
            self.floor_owner = FloorOwner.ASR
            self.engagement = Engagement.HOT

        elif event == Event.EMPTY_TRANSCRIPT:
            self.active_asr_job = False
            if self.floor_owner in (FloorOwner.USER, FloorOwner.ASR):
                self.floor_owner = FloorOwner.NONE
            self.engagement = Engagement.COOLDOWN
            self.conversation_heat = 1.0
            self._heat_set_time = time.monotonic()

        elif event == Event.FOREGROUND_GEN_START:
            task = kwargs.get("task")
            self.active_turn_task = task
            if self._current_turn and self._current_turn.status == TurnStatus.PENDING:
                self._current_turn.status = TurnStatus.ANSWERING
                self._current_turn.answer_started_at = time.monotonic()
            self.floor_owner = FloorOwner.ASSISTANT
            self.engagement = Engagement.HOT

        elif event == Event.FIRST_AUDIO_SENT:
            self.is_speaking_since = time.monotonic()
            self.floor_owner = FloorOwner.ASSISTANT
            self.engagement = Engagement.HOT

        elif event == Event.TTS_FINISHED:
            self.is_speaking_since = 0.0
            self.last_agent_audio_end = time.monotonic()
            # Close the current turn
            if self._current_turn and self._current_turn.status in (
                TurnStatus.PENDING, TurnStatus.ANSWERING
            ):
                self._current_turn.status = TurnStatus.ANSWERED
                self._current_turn.answer_ended_at = time.monotonic()
                self._turn_history.append(self._current_turn)
                self._current_turn = None
            self.active_turn_task = None
            # Start cooldown
            self.floor_owner = FloorOwner.NONE
            self.conversation_heat = 1.0
            self._heat_set_time = time.monotonic()
            self.engagement = Engagement.COOLDOWN
            self.last_attention_release = time.monotonic()

        elif event == Event.END_TURN:
            reason = kwargs.get("reason", "turn_dropped")
            if self._current_turn and self._current_turn.status in (
                TurnStatus.PENDING, TurnStatus.ANSWERING
            ):
                self._current_turn.status = TurnStatus.ERROR
                self._current_turn.failure_reason = reason
                self._current_turn.answer_ended_at = time.monotonic()
                self._turn_history.append(self._current_turn)
                self._current_turn = None
            self.active_turn_task = None
            if self.floor_owner == FloorOwner.ASSISTANT:
                self.floor_owner = FloorOwner.NONE
                self.last_attention_release = time.monotonic()
                self.conversation_heat = 1.0
                self._heat_set_time = time.monotonic()
                self.engagement = Engagement.COOLDOWN

        elif event == Event.BARGE_IN:
            reason = kwargs.get("reason", "unknown")
            self.floor_owner = FloorOwner.USER
            self.engagement = Engagement.HOT

        elif event == Event.MAINTENANCE_BEGIN:
            self.mode = SystemMode.MAINTENANCE

        elif event == Event.MAINTENANCE_END:
            self.mode = SystemMode.NORMAL
            if self.floor_owner == FloorOwner.BOOT:
                self.floor_owner = FloorOwner.NONE

        else:
            log.warning("Unknown event: %s", event)
            return

        # ── Post-transition bookkeeping ───────────────────────────────────────
        changed = (
            old_owner != self.floor_owner
            or old_engagement != self.engagement
            or old_mode != self.mode
        )
        if changed:
            self.floor_revision += 1
            self._revoke_all_leases()
            log.info(
                "floor: [%s/%s/%s] → [%s/%s/%s]  event=%s  rev=%d",
                old_owner.value, old_engagement.value, old_mode.value,
                self.floor_owner.value, self.engagement.value, self.mode.value,
                event.value, self.floor_revision,
            )

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    #  Backward-compatible named methods (thin wrappers around apply())
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    def mark_ready(self) -> None:
        """Call once at session startup after greeting completes."""
        self.apply(Event.MARK_READY)

    def on_onset(self) -> None:
        """AudioWorklet onset detected."""
        self.apply(Event.MIC_ONSET)

    def on_onset_end(self) -> None:
        """Onset ended without VAD speech."""
        self.apply(Event.MIC_ONSET_END)

    def on_vad_speech_start(self) -> None:
        """VAD confirmed speech in progress."""
        self.apply(Event.VAD_SPEECH_START)

    def on_vad_speech_end(self) -> None:
        """VAD says speech segment ended."""
        self.apply(Event.VAD_SPEECH_END)

    def on_transcript(self, turn_id: UUID) -> None:
        """A finalized ASR transcript arrived."""
        self.apply(Event.TRANSCRIPT_READY, turn_id=turn_id)

    def on_empty_transcript(self) -> None:
        """ASR returned nothing."""
        self.apply(Event.EMPTY_TRANSCRIPT)

    def start_foreground(self, task: asyncio.Task | None = None) -> None:
        """handle_utterance begins processing."""
        self.apply(Event.FOREGROUND_GEN_START, task=task)

    def set_speaking(self) -> None:
        """First TTS audio sent to browser."""
        self.apply(Event.FIRST_AUDIO_SENT)

    def on_tts_finished(self) -> None:
        """Browser confirmed playback complete."""
        self.apply(Event.TTS_FINISHED)

    def end_turn(self, reason: str = "turn_dropped") -> None:
        """Mark the turn as complete/errored (if tts_finished was not called)."""
        self.apply(Event.END_TURN, reason=reason)

    def enter_maintenance(self) -> None:
        """Self-edit starting."""
        self.apply(Event.MAINTENANCE_BEGIN)

    def exit_maintenance(self) -> None:
        """Self-edit complete."""
        self.apply(Event.MAINTENANCE_END)

    # ── Mode classification ───────────────────────────────────────────────────

    def set_mode(self, mode: TurnMode) -> None:
        """Set the turn complexity mode (called after ASR classification)."""
        self.turn_mode = mode

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    #  Derived state — the backward-compatible FloorState
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    @property
    def floor(self) -> FloorState:
        """Backward-compatible derived floor state.

        Maps from the three orthogonal axes to the legacy single enum.
        """
        if self.mode == SystemMode.MAINTENANCE:
            return FloorState.MAINTENANCE
        if self.floor_owner == FloorOwner.BOOT:
            return FloorState.BOOTING

        if self.floor_owner == FloorOwner.USER:
            if self.vad_hot:
                return FloorState.USER_SPEAKING
            return FloorState.USER_ACQUIRING

        if self.floor_owner == FloorOwner.ASR:
            return FloorState.ASR_PENDING

        if self.floor_owner == FloorOwner.ASSISTANT:
            if self.is_speaking_since > 0:
                return FloorState.AGENT_SPEAKING
            return FloorState.FOREGROUND_THINKING

        # FloorOwner.NONE
        if self.engagement == Engagement.COOLDOWN:
            return FloorState.COOLDOWN
        if self.engagement == Engagement.ENGAGED:
            return FloorState.ENGAGED_SILENCE
        return FloorState.TRULY_IDLE

    @property
    def pending_user_turn_id(self) -> Optional[UUID]:
        """Backward-compatible: the pending turn's UUID, or None."""
        if self._current_turn and self._current_turn.status in (
            TurnStatus.PENDING, TurnStatus.ANSWERING
        ):
            return self._current_turn.turn_id
        return None

    @pending_user_turn_id.setter
    def pending_user_turn_id(self, value: Optional[UUID]) -> None:
        """Backward-compatible setter for code that still assigns directly."""
        if value is None:
            if self._current_turn and self._current_turn.status in (
                TurnStatus.PENDING, TurnStatus.ANSWERING
            ):
                self._current_turn.status = TurnStatus.ANSWERED
                self._current_turn.answer_ended_at = time.monotonic()
                self._turn_history.append(self._current_turn)
                self._current_turn = None
        else:
            # Creating a new turn via legacy setter
            self._current_turn = TurnRecord(turn_id=value)

    @property
    def reply_debt(self) -> Optional[TurnRecord]:
        """The current unanswered turn, or None."""
        if self._current_turn and self._current_turn.status == TurnStatus.PENDING:
            return self._current_turn
        return None

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    #  Compound predicates — THE authority for scheduling
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    @property
    def background_eligible(self) -> bool:
        """True IFF the ambient mind is allowed to run (silent work, no speech).

        This is the ONLY authority for background cognition scheduling.
        """
        if self.floor_owner != FloorOwner.NONE:
            return False
        if self.engagement not in (Engagement.ENGAGED, Engagement.IDLE):
            return False
        if self.mode != SystemMode.NORMAL:
            return False
        if self._current_turn is not None and self._current_turn.status in (
            TurnStatus.PENDING, TurnStatus.ANSWERING
        ):
            return False
        if self.vad_hot or self.mic_onset_active:
            return False
        if self.active_asr_job:
            return False

        now = time.monotonic()
        if (now - self.last_attention_release) < self.MIN_IDLE_FOR_THOUGHT:
            return False

        return True

    @property
    def surface_eligible(self) -> bool:
        """True IFF initiative may speak.

        Stricter than background_eligible — requires IDLE engagement,
        presence confidence, and social budget.
        """
        if not self.background_eligible:
            return False
        if self.engagement != Engagement.IDLE:
            return False
        if self.presence.confidence < 0.5:
            return False
        if self.social_budget.remaining <= 0:
            return False
        return True

    @property
    def is_speaking(self) -> bool:
        """True if agent audio is actively playing."""
        return self.floor_owner == FloorOwner.ASSISTANT and self.is_speaking_since > 0

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    #  Lease management
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    def try_acquire_lease(self, kind: str, ttl: float = 5.0) -> Optional[BackgroundLease]:
        """Acquire a revocable lease for background/initiative work.

        Returns None if not eligible.  The lease becomes invalid on
        any state change (floor_revision bump).
        """
        if kind == "initiative_speak":
            if not self.surface_eligible:
                return None
        else:
            if not self.background_eligible:
                return None

        lease = BackgroundLease(
            id=uuid4(),
            kind=kind,
            granted_at=time.monotonic(),
            floor_revision=self.floor_revision,
            ttl=ttl,
            _supervisor=self,
        )
        self._active_leases.append(lease)
        return lease

    def _revoke_all_leases(self) -> None:
        """Revoke all outstanding leases.  Called on every state change."""
        for lease in self._active_leases:
            lease.revoked = True
        self._active_leases.clear()

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    #  Heat decay + automatic floor advancement
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    def tick(self) -> None:
        """Advance timed transitions.  Call periodically (e.g. every second)."""
        now = time.monotonic()

        # Decay conversation heat
        if self.conversation_heat > 0 and self._heat_set_time > 0:
            elapsed = now - self._heat_set_time
            self.conversation_heat = max(0.0, 1.0 - elapsed / self.COOLDOWN_SECONDS)

        # Advance COOLDOWN → ENGAGED
        if self.engagement == Engagement.COOLDOWN and self.floor_owner == FloorOwner.NONE:
            if self.conversation_heat <= 0:
                old_engagement = self.engagement
                self.engagement = Engagement.ENGAGED
                self.floor_revision += 1
                self._revoke_all_leases()
                log.info(
                    "tick: engagement %s → %s  rev=%d",
                    old_engagement.value, self.engagement.value, self.floor_revision,
                )

        # Advance ENGAGED → IDLE
        elif self.engagement == Engagement.ENGAGED and self.floor_owner == FloorOwner.NONE:
            since_release = now - self.last_attention_release
            if since_release > self.ENGAGED_SILENCE_SECONDS:
                old_engagement = self.engagement
                self.engagement = Engagement.IDLE
                self.floor_revision += 1
                self._revoke_all_leases()
                log.info(
                    "tick: engagement %s → %s  rev=%d",
                    old_engagement.value, self.engagement.value, self.floor_revision,
                )

        # is_speaking failsafe
        if (self.is_speaking_since > 0
                and (now - self.is_speaking_since) > self.IS_SPEAKING_TIMEOUT):
            log.warning("is_speaking failsafe: forced reset after %.0fs",
                        self.IS_SPEAKING_TIMEOUT)
            self.is_speaking_since = 0.0
            if self.floor_owner == FloorOwner.ASSISTANT:
                self.apply(Event.TTS_FINISHED)

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    #  Orphaned turn watchdog
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    def check_orphaned_turn(self) -> bool:
        """Returns True if an orphaned turn was detected and force-cleared."""
        if self._current_turn is None:
            return False
        if self._current_turn.status not in (TurnStatus.PENDING, TurnStatus.ANSWERING):
            return False

        elapsed = time.monotonic() - self._current_turn.transcript_at
        if elapsed > self.ORPHAN_TIMEOUT_SECONDS:
            log.critical(
                "ORPHANED TURN detected: turn_id=%s status=%s held for %.0fs — force-clearing",
                self._current_turn.turn_id, self._current_turn.status.value, elapsed,
            )
            self._current_turn.status = TurnStatus.TIMEOUT
            self._current_turn.failure_reason = "orphan_watchdog"
            self._turn_history.append(self._current_turn)
            self._current_turn = None
            self.orphaned_turn_count += 1

            if self.floor_owner in (FloorOwner.ASR, FloorOwner.ASSISTANT):
                self.floor_owner = FloorOwner.NONE
                self.engagement = Engagement.COOLDOWN
                self.conversation_heat = 1.0
                self._heat_set_time = time.monotonic()
                self.floor_revision += 1
                self._revoke_all_leases()

            return True
        return False

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    #  Primary preemption entry point
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    async def preempt(self, ws, reason: str = "") -> int:
        """Atomically halt all agent activity, purge queues, advance epoch.

        Returns the new epoch (callers should use this to tag future output).
        """
        self.turn_epoch += 1

        # Purge ghost utterances
        drained = 0
        while not self._utterance_queue.empty():
            try:
                self._utterance_queue.get_nowait()
                drained += 1
            except asyncio.QueueEmpty:
                break
        if drained:
            log.info("preempt: drained %d ghost utterance(s)", drained)

        # Cancel active turn task
        if self.active_turn_task and not self.active_turn_task.done():
            self.active_turn_task.cancel()
            try:
                await asyncio.wait_for(
                    asyncio.shield(self.active_turn_task), timeout=0.15
                )
            except (asyncio.CancelledError, asyncio.TimeoutError, Exception):
                pass

        # Cancel filler audio
        if self.filler_task and not self.filler_task.done():
            self.filler_task.cancel()

        # Clear speaking state
        self.is_speaking_since = 0.0
        self.active_turn_task = None

        # Supersede any pending turn on barge-in
        if self._current_turn and self._current_turn.status in (
            TurnStatus.PENDING, TurnStatus.ANSWERING
        ):
            self._current_turn.status = TurnStatus.SUPERSEDED
            self._current_turn.failure_reason = f"preempt:{reason}"
            self._turn_history.append(self._current_turn)
            self._current_turn = None

        # Signal browser to flush
        from pipeline._ws_helpers import safe_send_json
        await safe_send_json(ws, {
            "type": "stop_audio",
            "reason": reason,
            "epoch": self.turn_epoch,
        })

        # Transition to user acquiring
        self.floor_owner = FloorOwner.USER
        self.engagement = Engagement.HOT
        self.floor_revision += 1
        self._revoke_all_leases()

        log.info(
            "preempt → epoch=%d rev=%d reason=%s drained=%d",
            self.turn_epoch, self.floor_revision, reason, drained,
        )
        return self.turn_epoch

    # ── Safe mode ─────────────────────────────────────────────────────────────

    def check_safe_mode(self) -> bool:
        """Auto-degrade to safe mode if too many floor violations."""
        if self.floor_violation_count >= self.SAFE_MODE_THRESHOLD:
            if self.mode != SystemMode.SAFE_MODE:
                self.mode = SystemMode.SAFE_MODE
                self.floor_revision += 1
                self._revoke_all_leases()
                log.critical(
                    "SAFE_MODE engaged: %d floor violations in session",
                    self.floor_violation_count,
                )
            return True
        return False

    def record_floor_violation(self, detail: str = "") -> None:
        """Record a floor sovereignty violation."""
        self.floor_violation_count += 1
        log.error("Floor violation #%d: %s", self.floor_violation_count, detail)
        self.check_safe_mode()

    # ── Epoch discipline ──────────────────────────────────────────────────────

    def is_stale(self, epoch: int) -> bool:
        """True if the given epoch is no longer current."""
        return epoch != self.turn_epoch

    # ── Internal ──────────────────────────────────────────────────────────────

    def _touch_human_activity(self) -> None:
        """Update the last human activity timestamp + presence."""
        self.last_human_activity = time.monotonic()
        self.presence.confidence = min(1.0, self.presence.confidence + 0.3)
        self.presence.last_evidence = time.monotonic()
        self.presence.source = "recent_activity"
