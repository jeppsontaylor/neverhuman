"""
core/rumination_governor.py — Anti-rumination governor for GARY

Prevents the mind daemon from getting stuck in repetitive thought loops.
Works alongside ThoughtDeduplicator but at a higher level:

  ThoughtDeduplicator: rejects single duplicate thoughts (word-level)
  RuminationGovernor:  detects systemic patterns (topic-level, multi-cycle)

Intervention ladder:
  1. Gentle redirect: insert topic avoidance instruction
  2. Phase shift: force phase change (e.g. reflecting → brainstorming)
  3. Cooldown: pause mind daemon for N seconds
  4. Hard reset: clear recent thought history, start fresh

8/14 reviewers requested rumination control.
"""
from __future__ import annotations

import logging
import time
from collections import Counter, deque
from dataclasses import dataclass, field
from typing import Optional

log = logging.getLogger("gary.rumination")

# Thresholds
TOPIC_REPEAT_THRESHOLD = 3      # Same topic N times → intervention
WINDOW_SIZE = 20                # Look back N thoughts
COOLDOWN_DURATION_SEC = 120     # Pause duration for level-3 intervention
HARD_RESET_THRESHOLD = 5        # Topic appears this many times → hard reset


@dataclass
class RuminationState:
    """Current state of the rumination governor."""
    intervention_level: int = 0       # 0=none, 1=redirect, 2=phase_shift, 3=cooldown, 4=hard_reset
    avoided_topics: list[str] = field(default_factory=list)
    cooldown_until: float = 0.0
    forced_phase: Optional[str] = None
    total_interventions: int = 0
    last_intervention_ts: float = 0.0


class RuminationGovernor:
    """Detects and intervenes in thought rumination patterns."""

    def __init__(self, window_size: int = WINDOW_SIZE) -> None:
        self._recent_topics: deque[str] = deque(maxlen=window_size)
        self._topic_counter: Counter = Counter()
        self._state = RuminationState()

    @property
    def state(self) -> RuminationState:
        return self._state

    @property
    def is_in_cooldown(self) -> bool:
        return time.time() < self._state.cooldown_until

    @property
    def avoided_topics(self) -> list[str]:
        return list(self._state.avoided_topics)

    def record_thought(self, text: str) -> None:
        """Record a thought and extract its primary topic."""
        topic = self._extract_topic(text)
        if topic:
            self._recent_topics.append(topic)
            self._topic_counter[topic] += 1

    def check(self) -> RuminationState:
        """Check for rumination and set intervention level.

        Returns updated state with intervention recommendations.
        """
        if self.is_in_cooldown:
            return self._state

        # Find most repeated topic in window
        if not self._recent_topics:
            self._state.intervention_level = 0
            return self._state

        window_topics = list(self._recent_topics)
        freq = Counter(window_topics)
        most_common_topic, count = freq.most_common(1)[0]

        if count >= HARD_RESET_THRESHOLD:
            self._intervene(4, most_common_topic)
        elif count >= TOPIC_REPEAT_THRESHOLD + 1:
            self._intervene(3, most_common_topic)
        elif count >= TOPIC_REPEAT_THRESHOLD:
            self._intervene(2, most_common_topic)
        elif count >= TOPIC_REPEAT_THRESHOLD - 1:
            self._intervene(1, most_common_topic)
        else:
            self._state.intervention_level = 0
            self._state.forced_phase = None

        return self._state

    def _intervene(self, level: int, topic: str) -> None:
        """Apply intervention at the given level."""
        self._state.intervention_level = level
        self._state.total_interventions += 1
        self._state.last_intervention_ts = time.time()

        if topic not in self._state.avoided_topics:
            self._state.avoided_topics.append(topic)
            # Keep avoided_topics reasonably sized
            if len(self._state.avoided_topics) > 10:
                self._state.avoided_topics = self._state.avoided_topics[-10:]

        if level == 1:
            log.info("Rumination L1 (redirect): topic '%s' repeated", topic)
        elif level == 2:
            self._state.forced_phase = "brainstorming"
            log.info("Rumination L2 (phase shift → brainstorming): topic '%s'", topic)
        elif level == 3:
            self._state.cooldown_until = time.time() + COOLDOWN_DURATION_SEC
            log.info("Rumination L3 (cooldown %ds): topic '%s'", COOLDOWN_DURATION_SEC, topic)
        elif level == 4:
            self._state.cooldown_until = time.time() + COOLDOWN_DURATION_SEC * 2
            self.reset()
            log.warning("Rumination L4 (hard reset + %ds cooldown): topic '%s'", COOLDOWN_DURATION_SEC * 2, topic)

    def reset(self) -> None:
        """Clear all tracked topics (hard reset, level 4)."""
        self._recent_topics.clear()
        self._topic_counter.clear()
        self._state.intervention_level = 0
        self._state.forced_phase = None
        log.info("Rumination governor reset")

    def clear_cooldown(self) -> None:
        """Manually clear the cooldown (e.g. when user initiates conversation)."""
        self._state.cooldown_until = 0.0
        self._state.forced_phase = None

    def _extract_topic(self, text: str) -> Optional[str]:
        """Extract a rough topic key from thought text.

        Uses a simple bag-of-words approach with stop-word filtering.
        Not a full NLP pipeline — just enough to catch repetition.
        """
        if not text or len(text) < 10:
            return None

        stop_words = {
            "the", "a", "an", "is", "are", "was", "were", "be", "been",
            "being", "have", "has", "had", "do", "does", "did", "will",
            "would", "could", "should", "may", "might", "can", "shall",
            "to", "of", "in", "for", "on", "with", "at", "by", "from",
            "as", "into", "through", "during", "before", "after",
            "and", "but", "or", "not", "no", "so", "if", "then",
            "than", "too", "very", "just", "about", "that", "this",
            "it", "its", "i", "me", "my", "we", "our", "you", "your",
            "he", "she", "they", "them", "what", "which", "who",
            "how", "when", "where", "why", "all", "each", "every",
            "some", "any", "few", "more", "most", "other", "such",
        }

        words = text.lower().split()
        content_words = [w.strip(".,!?\"'()[]{}:;") for w in words if len(w) > 2]
        content_words = [w for w in content_words if w and w not in stop_words]

        if not content_words:
            return None

        # Take the 3 most frequent content words as a topic fingerprint
        freq = Counter(content_words)
        top = sorted(freq.items(), key=lambda x: (-x[1], x[0]))[:3]
        return "+".join(w for w, _ in top)

    def get_directive(self) -> Optional[str]:
        """Generate a prompt directive based on current intervention state.

        Returns None if no intervention is needed, or a string to inject
        into the mind daemon prompt.
        """
        if self._state.intervention_level == 0:
            return None

        if self._state.intervention_level == 1:
            topics = ", ".join(self._state.avoided_topics[-3:])
            return f"[GOVERNOR: Explore new territory. Avoid revisiting: {topics}]"

        if self._state.intervention_level == 2:
            topics = ", ".join(self._state.avoided_topics[-3:])
            return (
                f"[GOVERNOR: FORCED PHASE SHIFT. You are stuck on: {topics}. "
                f"Shift to completely different subject matter. Be creative.]"
            )

        if self._state.intervention_level >= 3:
            return "[GOVERNOR: Mind daemon is paused to break a thought loop. Resume shortly.]"

        return None
