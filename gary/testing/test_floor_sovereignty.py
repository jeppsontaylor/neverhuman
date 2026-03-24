"""
testing/test_floor_sovereignty.py — Phase 0 regression tests (v6)

Tests the invariants that the TurnSupervisor enforces:
  - No background mind pulse while reply_debt is set
  - No background mind pulse while floor_owner != NONE
  - No initiative while reply_debt is set
  - No initiative lease while surface_eligible is False
  - Conversation heat model prevents premature idle
  - Orphaned turn watchdog detects and clears stale turns
  - Floor state transitions are correct
  - Lease revocation on every state change
  - Safe mode after repeated violations
  - TurnRecord resolution tracking

These tests encode the 16:43 session failures as regression fixtures.
"""

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from pipeline.turn_supervisor import (
    TurnSupervisor, FloorState, FloorOwner, Engagement, SystemMode,
    Event, BackgroundLease, TurnRecord, TurnStatus,
)


@pytest.fixture
def supervisor():
    """Create a TurnSupervisor with a mock queue, ready for use."""
    q = asyncio.Queue()
    sup = TurnSupervisor(q)
    sup.mark_ready()
    return sup


# ── Floor state invariants ────────────────────────────────────────────────────

class TestBackgroundEligibility:
    """background_eligible is the ONLY authority for background cognition."""

    def test_idle_is_eligible(self, supervisor):
        """Background mind may run when floor_owner=NONE, engagement=IDLE."""
        assert supervisor.floor_owner == FloorOwner.NONE
        assert supervisor.engagement == Engagement.IDLE
        supervisor.last_attention_release = time.monotonic() - 20  # well past cooldown
        assert supervisor.background_eligible is True

    def test_not_eligible_during_user_speaking(self, supervisor):
        """No background cognition while user is speaking."""
        supervisor.apply(Event.VAD_SPEECH_START)
        assert supervisor.floor_owner == FloorOwner.USER
        assert supervisor.background_eligible is False

    def test_not_eligible_during_asr_pending(self, supervisor):
        """No background cognition while transcript is pending answer."""
        supervisor.apply(Event.TRANSCRIPT_READY, turn_id=uuid4())
        assert supervisor.floor_owner == FloorOwner.ASR
        assert supervisor.background_eligible is False

    def test_not_eligible_during_foreground_thinking(self, supervisor):
        """No background cognition while generating response."""
        supervisor.apply(Event.TRANSCRIPT_READY, turn_id=uuid4())
        supervisor.apply(Event.FOREGROUND_GEN_START, task=MagicMock())
        assert supervisor.floor_owner == FloorOwner.ASSISTANT
        assert supervisor.background_eligible is False

    def test_not_eligible_during_agent_speaking(self, supervisor):
        """No background cognition while agent is speaking."""
        supervisor.apply(Event.FIRST_AUDIO_SENT)
        assert supervisor.floor_owner == FloorOwner.ASSISTANT
        assert supervisor.background_eligible is False

    def test_not_eligible_during_cooldown(self, supervisor):
        """No background cognition during post-turn cooldown."""
        supervisor.apply(Event.TTS_FINISHED)
        assert supervisor.engagement == Engagement.COOLDOWN
        assert supervisor.background_eligible is False

    def test_eligible_during_engaged_silence(self, supervisor):
        """Background cognition IS allowed during engaged silence (silent work only)."""
        supervisor.engagement = Engagement.ENGAGED
        supervisor.floor_owner = FloorOwner.NONE
        supervisor.last_attention_release = time.monotonic() - 20
        assert supervisor.background_eligible is True

    def test_not_eligible_during_maintenance(self, supervisor):
        """No background cognition during self-edit."""
        supervisor.apply(Event.MAINTENANCE_BEGIN)
        assert supervisor.mode == SystemMode.MAINTENANCE
        assert supervisor.background_eligible is False

    def test_not_eligible_with_pending_turn(self, supervisor):
        """Reply debt blocks background even in IDLE."""
        supervisor.last_attention_release = time.monotonic() - 20
        supervisor.apply(Event.TRANSCRIPT_READY, turn_id=uuid4())
        # Force floor back to NONE without resolving turn
        supervisor.floor_owner = FloorOwner.NONE
        supervisor.engagement = Engagement.IDLE
        assert supervisor.background_eligible is False

    def test_not_eligible_with_vad_hot(self, supervisor):
        """Hot VAD blocks background."""
        supervisor.vad_hot = True
        supervisor.last_attention_release = time.monotonic() - 20
        assert supervisor.background_eligible is False

    def test_not_eligible_with_onset(self, supervisor):
        """Mic onset blocks background."""
        supervisor.mic_onset_active = True
        supervisor.last_attention_release = time.monotonic() - 20
        assert supervisor.background_eligible is False

    def test_not_eligible_too_soon_after_release(self, supervisor):
        """Must wait MIN_IDLE_FOR_THOUGHT after attention release."""
        supervisor.last_attention_release = time.monotonic() - 1  # only 1 second ago
        assert supervisor.background_eligible is False


# ── Surface eligibility (initiative) ──────────────────────────────────────────

class TestSurfaceEligibility:
    """surface_eligible gates initiative speech."""

    def test_surface_eligible_when_idle(self, supervisor):
        """Initiative allowed when fully idle + present + budget."""
        supervisor.last_attention_release = time.monotonic() - 20
        supervisor.presence.confidence = 0.8
        assert supervisor.surface_eligible is True

    def test_not_surface_eligible_during_engaged(self, supervisor):
        """Initiative NOT allowed during engaged silence."""
        supervisor.engagement = Engagement.ENGAGED
        supervisor.floor_owner = FloorOwner.NONE
        supervisor.last_attention_release = time.monotonic() - 20
        supervisor.presence.confidence = 0.8
        assert supervisor.surface_eligible is False

    def test_not_surface_eligible_low_presence(self, supervisor):
        """Initiative NOT allowed when user probably absent."""
        supervisor.last_attention_release = time.monotonic() - 20
        supervisor.presence.confidence = 0.2
        assert supervisor.surface_eligible is False

    def test_not_surface_eligible_budget_exhausted(self, supervisor):
        """Initiative NOT allowed when social budget is empty."""
        supervisor.last_attention_release = time.monotonic() - 20
        supervisor.presence.confidence = 0.8
        for _ in range(3):
            supervisor.social_budget.record_attempt()
        assert supervisor.surface_eligible is False


# ── Lease management ──────────────────────────────────────────────────────────

class TestLeaseManagement:
    """Revocable leases prevent time-of-check/time-of-use races."""

    def test_acquire_lease_when_eligible(self, supervisor):
        """Can acquire a lease when background_eligible."""
        supervisor.last_attention_release = time.monotonic() - 20
        lease = supervisor.try_acquire_lease("mind_pulse", ttl=5.0)
        assert lease is not None
        assert lease.valid is True

    def test_cannot_acquire_lease_when_not_eligible(self, supervisor):
        """Cannot acquire a lease when not background_eligible."""
        supervisor.apply(Event.VAD_SPEECH_START)
        lease = supervisor.try_acquire_lease("mind_pulse")
        assert lease is None

    def test_lease_revoked_on_state_change(self, supervisor):
        """Lease becomes invalid when floor state changes."""
        supervisor.last_attention_release = time.monotonic() - 20
        lease = supervisor.try_acquire_lease("mind_pulse", ttl=5.0)
        assert lease.valid is True

        # User starts speaking → all leases revoked
        supervisor.apply(Event.VAD_SPEECH_START)
        assert lease.valid is False

    def test_lease_expires_after_ttl(self, supervisor):
        """Lease expires after its TTL."""
        supervisor.last_attention_release = time.monotonic() - 20
        lease = supervisor.try_acquire_lease("mind_pulse", ttl=0.01)
        time.sleep(0.02)
        assert lease.valid is False

    def test_initiative_lease_requires_surface_eligible(self, supervisor):
        """Initiative lease requires surface_eligible, not just background_eligible."""
        supervisor.last_attention_release = time.monotonic() - 20
        supervisor.engagement = Engagement.ENGAGED
        supervisor.floor_owner = FloorOwner.NONE
        # background_eligible is True but surface_eligible is False
        assert supervisor.background_eligible is True
        assert supervisor.surface_eligible is False
        lease = supervisor.try_acquire_lease("initiative_speak", ttl=5.0)
        assert lease is None


# ── TurnRecord resolution ────────────────────────────────────────────────────

class TestTurnResolution:
    """TurnRecord tracks the full lifecycle of each user turn."""

    def test_transcript_creates_pending_turn(self, supervisor):
        """TRANSCRIPT_READY creates a PENDING TurnRecord."""
        tid = uuid4()
        supervisor.apply(Event.TRANSCRIPT_READY, turn_id=tid)
        assert supervisor._current_turn is not None
        assert supervisor._current_turn.turn_id == tid
        assert supervisor._current_turn.status == TurnStatus.PENDING

    def test_foreground_transitions_to_answering(self, supervisor):
        """FOREGROUND_GEN_START transitions to ANSWERING."""
        supervisor.apply(Event.TRANSCRIPT_READY, turn_id=uuid4())
        supervisor.apply(Event.FOREGROUND_GEN_START, task=MagicMock())
        assert supervisor._current_turn.status == TurnStatus.ANSWERING

    def test_tts_finished_transitions_to_answered(self, supervisor):
        """TTS_FINISHED transitions to ANSWERED and clears current turn."""
        supervisor.apply(Event.TRANSCRIPT_READY, turn_id=uuid4())
        supervisor.apply(Event.FOREGROUND_GEN_START, task=MagicMock())
        supervisor.apply(Event.FIRST_AUDIO_SENT)
        supervisor.apply(Event.TTS_FINISHED)
        assert supervisor._current_turn is None
        assert len(supervisor._turn_history) == 1
        assert supervisor._turn_history[0].status == TurnStatus.ANSWERED

    def test_new_transcript_supersedes_pending(self, supervisor):
        """A new transcript while one is pending supersedes the old one."""
        tid1 = uuid4()
        tid2 = uuid4()
        supervisor.apply(Event.TRANSCRIPT_READY, turn_id=tid1)
        supervisor.apply(Event.TRANSCRIPT_READY, turn_id=tid2)
        assert supervisor._current_turn.turn_id == tid2
        assert len(supervisor._turn_history) == 1
        assert supervisor._turn_history[0].status == TurnStatus.SUPERSEDED
        assert supervisor._turn_history[0].superseded_by == tid2

    def test_orphan_watchdog_sets_timeout(self, supervisor):
        """Orphan watchdog sets TurnStatus.TIMEOUT."""
        supervisor.apply(Event.TRANSCRIPT_READY, turn_id=uuid4())
        supervisor._current_turn.transcript_at = time.monotonic() - supervisor.ORPHAN_TIMEOUT_SECONDS - 1
        assert supervisor.check_orphaned_turn() is True
        assert supervisor.orphaned_turn_count == 1
        assert supervisor._current_turn is None
        assert supervisor._turn_history[-1].status == TurnStatus.TIMEOUT


# ── Conversation heat / cooldown model ────────────────────────────────────────

class TestConversationHeat:
    """Heat model prevents premature idle after conversation."""

    def test_heat_set_on_tts_finished(self, supervisor):
        """Conversation heat starts at 1.0 when TTS finishes."""
        supervisor.apply(Event.TTS_FINISHED)
        assert supervisor.conversation_heat == 1.0
        assert supervisor.engagement == Engagement.COOLDOWN

    def test_heat_decays_on_tick(self, supervisor):
        """Heat decays toward 0 after tick."""
        supervisor.apply(Event.TTS_FINISHED)
        supervisor._heat_set_time = time.monotonic() - (supervisor.COOLDOWN_SECONDS / 2)
        supervisor.tick()
        assert 0 < supervisor.conversation_heat < 1.0

    def test_cooldown_to_engaged(self, supervisor):
        """After heat decays to 0, engagement advances to ENGAGED."""
        supervisor.apply(Event.TTS_FINISHED)
        supervisor._heat_set_time = time.monotonic() - supervisor.COOLDOWN_SECONDS - 1
        supervisor.tick()
        assert supervisor.engagement == Engagement.ENGAGED

    def test_engaged_to_idle(self, supervisor):
        """After ENGAGED_SILENCE_SECONDS, engagement advances to IDLE."""
        supervisor.engagement = Engagement.ENGAGED
        supervisor.floor_owner = FloorOwner.NONE
        supervisor.last_attention_release = time.monotonic() - supervisor.ENGAGED_SILENCE_SECONDS - 1
        supervisor.tick()
        assert supervisor.engagement == Engagement.IDLE


# ── Orphaned turn watchdog ────────────────────────────────────────────────────

class TestOrphanedTurnWatchdog:
    """Every transcript must produce an answer. Never silence."""

    def test_no_orphan_when_recent(self, supervisor):
        """Recent pending turn is NOT orphaned."""
        supervisor.apply(Event.TRANSCRIPT_READY, turn_id=uuid4())
        assert supervisor.check_orphaned_turn() is False
        assert supervisor.orphaned_turn_count == 0

    def test_orphan_detected_after_timeout(self, supervisor):
        """Pending turn older than ORPHAN_TIMEOUT is force-cleared."""
        supervisor.apply(Event.TRANSCRIPT_READY, turn_id=uuid4())
        supervisor._current_turn.transcript_at = time.monotonic() - supervisor.ORPHAN_TIMEOUT_SECONDS - 1
        assert supervisor.check_orphaned_turn() is True
        assert supervisor.orphaned_turn_count == 1
        assert supervisor._current_turn is None


# ── Floor transition correctness ──────────────────────────────────────────────

class TestFloorTransitions:
    """State machine transitions are correct."""

    def test_boot_to_idle(self):
        """mark_ready() transitions from BOOT to NONE/IDLE."""
        sup = TurnSupervisor(asyncio.Queue())
        assert sup.floor_owner == FloorOwner.BOOT
        sup.mark_ready()
        assert sup.floor_owner == FloorOwner.NONE
        assert sup.engagement == Engagement.IDLE

    def test_onset_transitions(self, supervisor):
        """Onset → USER."""
        supervisor.apply(Event.MIC_ONSET)
        assert supervisor.floor_owner == FloorOwner.USER
        assert supervisor.mic_onset_active is True

    def test_vad_speech_transitions(self, supervisor):
        """VAD speech → USER + vad_hot."""
        supervisor.apply(Event.VAD_SPEECH_START)
        assert supervisor.floor_owner == FloorOwner.USER
        assert supervisor.vad_hot is True

    def test_transcript_transitions(self, supervisor):
        """Transcript → ASR with TurnRecord."""
        tid = uuid4()
        supervisor.apply(Event.TRANSCRIPT_READY, turn_id=tid)
        assert supervisor.floor_owner == FloorOwner.ASR
        assert supervisor.pending_user_turn_id == tid

    def test_foreground_transitions(self, supervisor):
        """Foreground start → ASSISTANT."""
        supervisor.apply(Event.TRANSCRIPT_READY, turn_id=uuid4())
        mock = MagicMock()
        supervisor.apply(Event.FOREGROUND_GEN_START, task=mock)
        assert supervisor.floor_owner == FloorOwner.ASSISTANT
        assert supervisor.active_turn_task == mock

    def test_speaking_transitions(self, supervisor):
        """FIRST_AUDIO_SENT → ASSISTANT + is_speaking."""
        supervisor.apply(Event.FIRST_AUDIO_SENT)
        assert supervisor.floor_owner == FloorOwner.ASSISTANT
        assert supervisor.is_speaking is True

    def test_tts_finished_clears_debt(self, supervisor):
        """TTS_FINISHED clears pending turn and starts cooldown."""
        supervisor.apply(Event.TRANSCRIPT_READY, turn_id=uuid4())
        supervisor.apply(Event.FOREGROUND_GEN_START, task=MagicMock())
        supervisor.apply(Event.FIRST_AUDIO_SENT)
        supervisor.apply(Event.TTS_FINISHED)
        assert supervisor.pending_user_turn_id is None
        assert supervisor.engagement == Engagement.COOLDOWN
        assert supervisor.floor_owner == FloorOwner.NONE

    def test_derived_floor_state_backward_compat(self, supervisor):
        """The derived `floor` property matches legacy FloorState."""
        assert supervisor.floor == FloorState.TRULY_IDLE

        supervisor.apply(Event.MIC_ONSET)
        assert supervisor.floor == FloorState.USER_ACQUIRING

        supervisor.apply(Event.VAD_SPEECH_START)
        assert supervisor.floor == FloorState.USER_SPEAKING

        supervisor.apply(Event.VAD_SPEECH_END)
        tid = uuid4()
        supervisor.apply(Event.TRANSCRIPT_READY, turn_id=tid)
        assert supervisor.floor == FloorState.ASR_PENDING

        supervisor.apply(Event.FOREGROUND_GEN_START, task=MagicMock())
        assert supervisor.floor == FloorState.FOREGROUND_THINKING

        supervisor.apply(Event.FIRST_AUDIO_SENT)
        assert supervisor.floor == FloorState.AGENT_SPEAKING

        supervisor.apply(Event.TTS_FINISHED)
        assert supervisor.floor == FloorState.COOLDOWN


# ── Safe mode ─────────────────────────────────────────────────────────────────

class TestSafeMode:
    """Safe mode auto-degrades on repeated violations."""

    def test_safe_mode_engages_after_threshold(self, supervisor):
        """3 violations triggers safe mode."""
        for i in range(3):
            supervisor.record_floor_violation(f"test_{i}")
        assert supervisor.mode == SystemMode.SAFE_MODE
        assert supervisor.background_eligible is False

    def test_safe_mode_not_premature(self, supervisor):
        """2 violations does not trigger safe mode."""
        for i in range(2):
            supervisor.record_floor_violation(f"test_{i}")
        assert supervisor.mode == SystemMode.NORMAL


# ── 16:43 session regression ─────────────────────────────────────────────────

class TestSessionRegression:
    """
    Regression for the 16:43 session failures.
    The mind fired a background pulse while the user was actively speaking
    and a transcript was pending answer.
    """

    def test_no_mind_pulse_during_active_conversation(self, supervisor):
        """Simulate the exact failure: user speaks, transcript pending, mind tries to pulse."""
        # User starts speaking
        supervisor.apply(Event.MIC_ONSET)
        assert supervisor.background_eligible is False

        # VAD confirms speech
        supervisor.apply(Event.VAD_SPEECH_START)
        assert supervisor.background_eligible is False

        # Speech ends, VAD finalizes utterance
        supervisor.apply(Event.VAD_SPEECH_END)

        # Transcript arrives — conversation debt created
        turn_id = uuid4()
        supervisor.apply(Event.TRANSCRIPT_READY, turn_id=turn_id)
        assert supervisor.background_eligible is False
        assert supervisor.pending_user_turn_id == turn_id

        # System starts generating response
        supervisor.apply(Event.FOREGROUND_GEN_START, task=MagicMock())
        assert supervisor.background_eligible is False

        # TTS starts playing
        supervisor.apply(Event.FIRST_AUDIO_SENT)
        assert supervisor.background_eligible is False

        # TTS finishes — cooldown begins, not idle yet
        supervisor.apply(Event.TTS_FINISHED)
        assert supervisor.engagement == Engagement.COOLDOWN
        assert supervisor.background_eligible is False

        # Wait for cooldown → engaged silence → eligible for silent work
        supervisor._heat_set_time = time.monotonic() - supervisor.COOLDOWN_SECONDS - 1
        supervisor.tick()
        assert supervisor.engagement == Engagement.ENGAGED
        supervisor.last_attention_release = time.monotonic() - 20
        assert supervisor.background_eligible is True  # but NOT surface_eligible

        # Wait for engaged → idle → NOW surface eligible too
        supervisor.last_attention_release = time.monotonic() - supervisor.ENGAGED_SILENCE_SECONDS - supervisor.MIN_IDLE_FOR_THOUGHT - 1
        supervisor.tick()
        assert supervisor.engagement == Engagement.IDLE
        assert supervisor.background_eligible is True

    def test_lease_prevents_stale_initiative(self, supervisor):
        """A lease acquired in IDLE is revoked when the user starts speaking."""
        supervisor.last_attention_release = time.monotonic() - 20
        supervisor.presence.confidence = 0.8
        lease = supervisor.try_acquire_lease("initiative_speak", ttl=10.0)
        assert lease is not None
        assert lease.valid is True

        # User starts speaking — lease should be revoked
        supervisor.apply(Event.VAD_SPEECH_START)
        assert lease.valid is False


# ── Initiative isolation ──────────────────────────────────────────────────────

class TestInitiativeIsolation:
    """Initiative must never go through handle_utterance."""

    def test_initiative_blocked_during_pending_turn(self, supervisor):
        """Initiative cannot surface while conversation debt exists."""
        supervisor.apply(Event.TRANSCRIPT_READY, turn_id=uuid4())
        # Force floor to NONE without resolving turn
        supervisor.floor_owner = FloorOwner.NONE
        supervisor.engagement = Engagement.IDLE
        supervisor.last_attention_release = time.monotonic() - 20
        assert supervisor.background_eligible is False

    def test_initiative_blocked_during_user_speaking(self, supervisor):
        """Initiative cannot surface while user is speaking."""
        supervisor.apply(Event.VAD_SPEECH_START)
        assert supervisor.background_eligible is False

