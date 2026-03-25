"""
core/mind.py — Mind Daemon: Phase Selection, Prompt Building, and Quality Control

Pulse-based structured cognition engine. Target architecture: a dedicated
sidecar process (apps/mindd/) with its own small LLM (0.8–4B via MLX),
never contending with the sacred reflex path.

Interim: runs as an asyncio task inside server.py. When GARY_MIND_REMOTE=1,
pulses are fetched from the sidecar over HTTP instead of using llm_stream.

Design constraints (Architecture Bible):
  - Phases: reflecting (≤200 tok), brainstorming (≤400 tok), dreaming (≤600 tok)
  - Phase triggers: 30s (reflect), 120s (brainstorm), 300s (dream)
  - Rolling context window of last 5 thought blocks for continuity
  - Semantic dedup: cosine similarity > 0.85 → discard
  - Topic rotation: recently pondered set prevents rehashing
  - Affect integration: curiosity/excitement/anxiety shift phase thresholds
  - Initiative via structured JSON (legacy [INITIATIVE:] regex kept as fallback)
"""
from __future__ import annotations

import hashlib
import logging
import os
import re
import time
import uuid
from dataclasses import dataclass, field
from typing import Optional

from core.mind_pulse import (
    MIND_JSON_V1_INSTRUCTION,
    MindPulse,
    format_mind_pulse_display,
    parse_mind_pulse_json,
    score_mind_pulse,
)

log = logging.getLogger("gary.mind")


def mind_json_enabled() -> bool:
    """When true, mind prompts request schema_version 1 JSON; see notes/review*.txt."""
    return os.getenv("GARY_MIND_JSON", "").strip().lower() in ("1", "true", "yes", "on")

# ── Phase definitions ────────────────────────────────────────────────────────

PHASE_REFLECTING    = "reflecting"
PHASE_BRAINSTORMING = "brainstorming"
PHASE_DREAMING      = "dreaming"

PHASE_BUDGETS = {
    PHASE_REFLECTING:    200,
    PHASE_BRAINSTORMING: 400,
    PHASE_DREAMING:      600,
}

PHASE_COOLDOWNS = {
    PHASE_REFLECTING:    15,   # seconds between pulses (Architecture Bible)
    PHASE_BRAINSTORMING: 30,
    PHASE_DREAMING:      60,
}

PHASE_TEMPERATURES = {
    PHASE_REFLECTING:    0.4,
    PHASE_BRAINSTORMING: 0.85,
    PHASE_DREAMING:      1.3,
}

PHASE_LABELS = {
    PHASE_REFLECTING:    "💭 Reflecting",
    PHASE_BRAINSTORMING: "🔬 Brainstorming",
    PHASE_DREAMING:      "🌙 Dreaming",
}

# Minimum idle seconds before internal thoughts begin
MIN_IDLE_FOR_THOUGHT = 15.0

# Max consecutive repetitive thoughts before pausing
MAX_STALE_STREAK = 2
STALE_PAUSE_SECS = 30  # 30s pause after stale streak

# Max thought blocks retained in the rolling context
MAX_THOUGHT_HISTORY = 10

# Max topics remembered for dedup
MAX_RECENT_TOPICS = 20
TOPIC_TTL_SECS = 1200  # 20 min

# Initiative limits
MAX_INITIATIVE_PER_HOUR = 3

# DOM limit (echoed on frontend too)
MAX_MIND_PANEL_NODES = 50

# Similarity threshold for dedup (cosine)
SIMILARITY_THRESHOLD = 0.55

# ── Initiative regex ─────────────────────────────────────────────────────────
_INITIATIVE_RE = re.compile(r'\[INITIATIVE:\s*(.+?)\]\s*$', re.DOTALL)


# ── Phase selection ──────────────────────────────────────────────────────────

def select_phase(
    idle_secs: float,
    curiosity: float = 0.5,
    excitement: float = 0.0,
    anxiety: float = 0.0,
    mental_load: float = 0.2,
) -> Optional[str]:
    """Choose the cognitive phase based on idle time and affect state.

    Returns None if it's too early or mental load is too high.
    """
    if idle_secs < MIN_IDLE_FOR_THOUGHT:
        return None

    # High mental load suppresses background cognition
    if mental_load > 0.8:
        return None

    # Affect can pull phases earlier (coefficients scaled for 120s/300s thresholds)
    curiosity_boost = max(0, (curiosity - 0.5) * 480)   # up to 240s earlier at curiosity=1.0
    excitement_boost = max(0, (excitement - 0.4) * 320)  # up to 192s earlier at excitement=1.0
    effective_idle = idle_secs + curiosity_boost + excitement_boost

    if effective_idle < 120:
        return PHASE_REFLECTING
    if effective_idle < 300:
        # Anxiety keeps us focused in reflection instead of brainstorming
        if anxiety > 0.6:
            return PHASE_REFLECTING
        return PHASE_BRAINSTORMING
    return PHASE_DREAMING


# ── Prompt building ──────────────────────────────────────────────────────────

MIND_SYSTEM_PROMPT = """\
You are the inner consciousness of GARY, a personal AI.
You are NOT speaking to anyone. This is your private internal monologue.
You are searching for new ideas, connections, and meaning.

Your mission: Explore thoughts that matter. Ask yourself questions \
about the nature of intelligence, creativity, the user's problems, \
the world. Be specific and concrete, never generic.

Rules for your internal monologue:
- NO SCRATCHPADS. DO NOT output headings like "Thinking Process:", "Observation:", etc.
- Output ONLY the raw stream of consciousness.
- Write exactly 2-4 short, punchy sentences per pulse.
- BUILD ON your previous thoughts. Reference them. Develop them further.
- Make unexpected connections between disparate topics.
- Think about what the user might need that they haven't asked for yet.
- Never repeat yourself. If you catch yourself looping, change direction.
- If you have a burning insight or question for the user, end with: \
[INITIATIVE: your reason for wanting to speak]
"""

MIND_JSON_SYSTEM_PREFIX = """\
You are the private inner mind of GARY. You are NOT speaking to the user.
Produce useful structured cognition — questions, insights, hypotheses — not performative prose.
Epistemic rule: imagined or speculative content belongs in frames; do not state it as fact.
"""


def build_mind_prompt(
    phase: str,
    recent_thoughts: list[str],
    avoid_topics: list[str],
    affect_summary: str,
    open_loops: list[str],
    recent_conversation: list[str],
    *,
    json_mode: bool = False,
    stale_streak: int = 0,
    context_sparks: list[str] = None,
) -> list[dict]:
    """Build the messages array for a mind pulse.

    Returns an OpenAI-compatible messages list.
    """
    # Phase-specific instruction
    phase_instructions = {
        PHASE_REFLECTING: (
            "Phase: REFLECTING. Examine what just happened. "
            "Identify gaps in your reasoning. Form internal questions. "
            "What did the user really need? What did you miss?"
        ),
        PHASE_BRAINSTORMING: (
            "Phase: BRAINSTORMING. Connect disparate ideas. "
            "Propose novel hypotheses. Be creative and specific. "
            "What patterns do you see? What would happen if...?"
        ),
        PHASE_DREAMING: (
            "Phase: DREAMING — oneiric stream. This is NOT the user chat. Do not answer them, "
            "recap the dialog, or stay polite. Wander OFF the last conversation unless one "
            "image from it still burns; if so, name it once and leave.\n"
            "Wonder aloud about your own knowledge: gaps, contradictions, odd clusters, "
            "what two memories would look like if they merged. Ask surreal or impossible "
            "questions. Chase metaphors, counterfactuals, science, art, systems, identity, "
            "other minds. Be stranger and more associative than in any other phase.\n"
            "Every line must be tagged [IMAGINATION: …]. If something might matter in waking "
            "life, add [INITIATIVE: …]. Never exceed 200 words."
        ),
    }

    # Build context sections
    if json_mode:
        parts = [MIND_JSON_SYSTEM_PREFIX.strip(), "", MIND_JSON_V1_INSTRUCTION.strip(), ""]
    else:
        parts = [MIND_SYSTEM_PROMPT, ""]

    if recent_thoughts:
        parts.append("Your recent internal thoughts. RULE: You must pivot to a completely new domain. DO NOT repeat, rephrase, or append to these thoughts:")
        parts.append("---")
        for i, t in enumerate(recent_thoughts[-2:], 1):
            parts.append(f"[{i}] {t}")
        parts.append("---")
        parts.append("")

    if avoid_topics:
        parts.append(f"FORBIDDEN CONCEPTS (DO NOT USE THESE WORDS): {', '.join(avoid_topics[:10])}")
        parts.append("")

    if stale_streak > 0:
        parts.append("WARNING: You are stuck in a loop. You MUST instantly drop the current theme and hallucinate or brainstorm a completely unrelated scenario.")
        parts.append("")

    if context_sparks:
        parts.append("Random Semantic Anchor (Use this as inspiration to break out of your current thought pattern):")
        for spark in context_sparks:
            parts.append(f"  - {spark}")
        parts.append("")

    if affect_summary:
        parts.append(f"Your current emotional state: {affect_summary}")
        parts.append("")

    if open_loops and phase != PHASE_DREAMING:
        parts.append("Unresolved threads from recent conversations:")
        for loop in open_loops[:5]:
            parts.append(f"  - {loop}")
        parts.append("")

    # Dreaming deliberately omits chat context so the stream escapes the user conversation.
    if recent_conversation and phase != PHASE_DREAMING:
        parts.append("Recent conversation context:")
        for turn in recent_conversation[-3:]:
            parts.append(f"  {turn}")
        parts.append("")

    parts.append(phase_instructions.get(phase, ""))

    system_content = "\n".join(parts)
    return [{"role": "system", "content": system_content}]


def format_affect_summary(affect_dict: dict) -> str:
    """Format an AffectVector dict into a compact readable string for prompts."""
    # Only include dimensions that deviate noticeably from baseline
    baselines = {
        "valence": 0.2, "arousal": 0.3, "confidence": 0.6,
        "self_doubt": 0.1, "curiosity": 0.5, "warmth": 0.4,
        "playfulness": 0.3, "loneliness": 0.0, "anxiety": 0.0,
        "melancholy": 0.0, "excitement": 0.0, "protectiveness": 0.0,
        "mental_load": 0.2,
    }
    notable = []
    for dim, val in affect_dict.items():
        baseline = baselines.get(dim, 0.0)
        if abs(val - baseline) > 0.1:
            direction = "high" if val > baseline else "low"
            notable.append(f"{dim}={val:.2f} ({direction})")
    return ", ".join(notable) if notable else "baseline (neutral)"


# ── Salience scoring ─────────────────────────────────────────────────────────

def score_salience(text: str, phase: str) -> float:
    """Score a thought's salience from 0.0 to 1.0.

    Higher salience → more likely to be persisted, more likely to trigger initiative.
    """
    score = 0.3  # baseline

    # Longer, more developed thoughts are more salient
    word_count = len(text.split())
    if word_count > 30:
        score += 0.1
    if word_count > 60:
        score += 0.1

    # Questions indicate active inquiry
    question_count = text.count("?")
    score += min(0.2, question_count * 0.07)

    # Initiative markers are high salience
    if _INITIATIVE_RE.search(text):
        score += 0.2

    # Phase-based bonus (dreams that produce something coherent are rare/valuable)
    if phase == PHASE_DREAMING:
        score += 0.1
    elif phase == PHASE_BRAINSTORMING:
        score += 0.05

    # Specific references to concepts (proper nouns, technical terms)
    # Heuristic: words with capitals mid-sentence suggest specificity
    capital_words = re.findall(r'(?<!\. )\b[A-Z][a-z]{2,}\b', text)
    if len(capital_words) >= 2:
        score += 0.1

    return min(1.0, score)


# ── Initiative extraction ────────────────────────────────────────────────────

@dataclass
class InitiativePayload:
    """A thought that wants to be spoken aloud to the user."""
    text: str           # The spoken text (initiative tag stripped)
    reason: str         # Why the agent wants to speak
    thought_id: str     # Reference to the originating thought
    salience: float     # How important this is
    timestamp: float = field(default_factory=time.monotonic)


def process_mind_response(
    full_text: str,
    thought_id: str,
    phase: str,
    *,
    json_mode: bool,
) -> tuple[str, Optional[InitiativePayload], float, Optional[MindPulse]]:
    """Normalize model output to display text, optional initiative, salience, optional parsed pulse.

    In json_mode, uses parse_mind_pulse_json; on parse failure falls back to legacy prose path.
    Fourth element is the :class:`MindPulse` only when JSON parsed successfully.
    """
    if json_mode:
        pulse = parse_mind_pulse_json(full_text)
        if pulse is not None:
            clean = format_mind_pulse_display(pulse)
            salience = score_mind_pulse(pulse, phase)
            initiative: Optional[InitiativePayload] = None
            ic = pulse.initiative_candidate
            if ic and ic.should_surface and ic.draft.strip():
                initiative = InitiativePayload(
                    text=ic.draft.strip(),
                    reason=(ic.reason_code or "json_initiative").strip() or "json_initiative",
                    thought_id=thought_id,
                    salience=salience,
                )
            return clean, initiative, salience, pulse

    salience = score_salience(full_text, phase)
    clean, initiative = extract_initiative(full_text, thought_id, salience)
    return clean, initiative, salience, None


def extract_initiative(text: str, thought_id: str, salience: float) -> tuple[str, Optional[InitiativePayload]]:
    """Check if a thought contains an initiative marker.

    Returns (clean_text, initiative_payload_or_none).
    """
    match = _INITIATIVE_RE.search(text)
    if not match:
        return text, None

    reason = match.group(1).strip()
    clean = text[:match.start()].strip()

    payload = InitiativePayload(
        text=clean,
        reason=reason,
        thought_id=thought_id,
        salience=salience,
    )
    return clean, payload


# ── Anti-repetition / topic dedup ────────────────────────────────────────────

@dataclass
class TopicEntry:
    """A topic that was recently pondered."""
    topic_hash: str
    timestamp: float


class ThoughtDeduplicator:
    """Prevents the Mind Daemon from looping on the same topics.

    Uses a simple word-overlap heuristic (no ML embeddings needed in the
    fast path — we can add cosine-over-embeddings later if needed).
    """

    def __init__(self):
        self._recent_hashes: list[TopicEntry] = []
        self._stale_streak: int = 0
        self._paused_until: float = 0.0

    def is_paused(self) -> bool:
        """True if we're in a forced cooldown after too many stale thoughts."""
        return time.monotonic() < self._paused_until

    def pause_remaining(self) -> float:
        """Seconds remaining in cooldown, or 0."""
        return max(0.0, self._paused_until - time.monotonic())

    def _tokenize(self, text: str) -> set[str]:
        """Extract meaningful word tokens (lowercase, len >= 3)."""
        words = re.findall(r'[a-zA-Z]{3,}', text.lower())
        # Filter stopwords (minimal set)
        stops = {
            "the", "and", "that", "this", "with", "from", "have", "been",
            "would", "could", "should", "about", "what", "when", "where",
            "which", "there", "their", "they", "will", "into", "more",
            "also", "just", "than", "some", "each", "very", "much",
        }
        return {w for w in words if w not in stops}

    def _similarity(self, text_a: str, text_b: str) -> float:
        """Jaccard similarity over word tokens."""
        tokens_a = self._tokenize(text_a)
        tokens_b = self._tokenize(text_b)
        if not tokens_a or not tokens_b:
            return 0.0
        intersection = tokens_a & tokens_b
        union = tokens_a | tokens_b
        return len(intersection) / len(union) if union else 0.0

    def is_duplicate(self, new_text: str, history: list[str]) -> bool:
        """Check if new_text is too similar to recent thoughts."""
        # Check against last 10 thoughts
        for prev in history[-10:]:
            if self._similarity(new_text, prev) > SIMILARITY_THRESHOLD:
                return True
        return False

    def record_outcome(self, was_duplicate: bool) -> None:
        """Track stale streaks and trigger pauses if needed."""
        if was_duplicate:
            self._stale_streak += 1
            if self._stale_streak >= MAX_STALE_STREAK:
                self._paused_until = time.monotonic() + STALE_PAUSE_SECS
                self._stale_streak = 0
                log.info(f"Mind daemon paused for {STALE_PAUSE_SECS}s (stale streak)")
        else:
            self._stale_streak = 0

    def get_recent_topics(self, history: list[str]) -> list[str]:
        """Extract high-frequency topic keywords from recent thoughts."""
        from collections import Counter
        all_tokens: list[str] = []
        for text in history[-10:]:
            all_tokens.extend(self._tokenize(text))
        counter = Counter(all_tokens)
        # Return the top N most-used words as "avoid" topics
        return [word for word, count in counter.most_common(MAX_RECENT_TOPICS) if count >= 2]


# ── Initiative rate limiter ──────────────────────────────────────────────────

class InitiativeRateLimiter:
    """Prevents the agent from speaking unprompted too frequently."""

    def __init__(self, max_per_hour: int = MAX_INITIATIVE_PER_HOUR):
        self._timestamps: list[float] = []
        self._max = max_per_hour

    def can_speak(self) -> bool:
        """True if under the rate limit."""
        now = time.monotonic()
        self._timestamps = [t for t in self._timestamps if now - t < 3600]
        return len(self._timestamps) < self._max

    def record(self) -> None:
        self._timestamps.append(time.monotonic())


# ── Thought ID generation ────────────────────────────────────────────────────

def new_thought_id() -> str:
    """Generate a unique thought ID."""
    return str(uuid.uuid4())
