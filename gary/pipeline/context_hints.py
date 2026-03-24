"""
pipeline/context_hints.py — ASR context hint injection for GARY v2

Provides runtime vocabulary hints to Qwen3-ASR to improve transcription
of user-specific jargon, names, and technical terms.

This is a significant win for ASR accuracy: it biases the model toward
recognizing specific words without any fine-tuning.

Design:
  - Static hints: common domain terms that are always relevant
  - User hints: loaded from the user's personal lexicon (Memory Spine)
  - Session hints: accumulated during the conversation

Usage:
    hints = ContextHints()
    hints.add_user_terms(["GARY", "flash-moe", "Qwen"])
    hints.add_session_term("pgvector")

    # Pass to ASR at transcription time:
    context = hints.get_context_string()
    result = session.transcribe(audio, context=context)
"""
from __future__ import annotations

import logging
from typing import Set

log = logging.getLogger("gary.context_hints")


# Common technical domain terms that GARY should always recognize
_STATIC_HINTS = {
    "GARY", "Qwen", "MLX", "ONNX", "Metal",
    "pgvector", "Postgres", "Docker",
    "TTS", "ASR", "VAD", "LLM",
    "Kokoro", "Silero",
    "SSD", "pread", "madvise",
    "WebSocket", "FastAPI", "uvicorn",
    "LoRA", "QLoRA",
    "MoE", "MoE model",
    "flash-moe",
}


class ContextHints:
    """Manages ASR context hints for improved transcription accuracy."""

    def __init__(self):
        self._static: Set[str] = set(_STATIC_HINTS)
        self._user: Set[str] = set()       # from Memory Spine / dossier
        self._session: Set[str] = set()     # accumulated this session
        self._max_hints = 200               # don't overwhelm the ASR context window

    def add_user_terms(self, terms: list[str]) -> None:
        """Add user-specific vocabulary (names, jargon, project terms)."""
        self._user.update(terms)
        if len(self._user) > self._max_hints:
            # Keep most recently added
            self._user = set(list(self._user)[-self._max_hints:])

    def add_session_term(self, term: str) -> None:
        """Add a term discovered during this session."""
        self._session.add(term)

    def add_session_terms_from_text(self, text: str) -> None:
        """Extract potential proper nouns and technical terms from transcribed text.
        Simple heuristic: words that start with uppercase (beyond sentence start)
        or contain special characters like hyphens.
        """
        words = text.split()
        for i, word in enumerate(words):
            clean = word.strip(".,!?;:\"'()")
            if len(clean) < 2:
                continue
            # Proper nouns (capitalized, not at sentence start)
            if i > 0 and clean[0].isupper() and not clean.isupper():
                self._session.add(clean)
            # Technical terms with hyphens/underscores
            if "-" in clean or "_" in clean:
                self._session.add(clean)
            # All-caps acronyms (3+ chars)
            if clean.isupper() and len(clean) >= 3:
                self._session.add(clean)

    def get_context_string(self) -> str:
        """Build the context string for ASR.

        Format: comma-separated terms, most specific first.
        """
        all_terms = list(self._session) + list(self._user) + list(self._static)
        # Deduplicate while preserving order
        seen = set()
        unique = []
        for t in all_terms:
            if t.lower() not in seen:
                seen.add(t.lower())
                unique.append(t)
        # Truncate to max hints
        unique = unique[:self._max_hints]
        return ", ".join(unique)

    def clear_session(self) -> None:
        """Clear session-specific hints."""
        self._session.clear()

    def get_counts(self) -> dict:
        return {
            "static": len(self._static),
            "user": len(self._user),
            "session": len(self._session),
            "total_unique": len(set(
                [t.lower() for t in self._static | self._user | self._session]
            )),
        }
