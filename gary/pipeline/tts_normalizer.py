"""
pipeline/tts_normalizer.py — Pre-TTS text normalization

Replaces abbreviations, acronyms, and technical terms with their
spoken-aloud equivalents before sending text to the TTS engine.

Design:
  - JSON lookup loaded once at import → O(1) per key
  - Compiled regex for whole-word matching → scales to any dict size
  - Case-sensitive matching (preserves acronym identity)
  - Called on each sentence before TTS synthesis
"""

import json
import logging
import re
from pathlib import Path

log = logging.getLogger("gary.tts_normalizer")

_DICT_PATH = Path(__file__).parent / "tts_normalize.json"

def _load_dict() -> dict[str, str]:
    """Load the normalization dictionary from JSON."""
    try:
        with open(_DICT_PATH, encoding="utf-8") as f:
            d = json.load(f)
        log.info("TTS normalizer loaded %d entries", len(d))
        return d
    except Exception as exc:
        log.warning("Failed to load tts_normalize.json: %s — normalization disabled", exc)
        return {}

_NORM_DICT: dict[str, str] = _load_dict()

# Build a single compiled regex that matches any key as a whole word.
# Keys with special regex chars (like "e.g.") are escaped.
# Sort by length descending so longer keys match first (e.g. "CI/CD" before "CI").
def _build_pattern(d: dict[str, str]) -> re.Pattern | None:
    if not d:
        return None
    # Sort keys by length descending for greedy matching
    sorted_keys = sorted(d.keys(), key=len, reverse=True)
    # Escape each key and wrap in word boundaries where appropriate
    escaped = []
    for key in sorted_keys:
        ek = re.escape(key)
        # For keys that start/end with word chars, use word boundaries
        # For keys with special chars (e.g., "e.g."), use lookahead/behind
        if key[0].isalnum() and key[-1].isalnum():
            escaped.append(rf"\b{ek}\b")
        elif key[0].isalnum():
            escaped.append(rf"\b{ek}")
        elif key[-1].isalnum():
            escaped.append(rf"{ek}\b")
        else:
            # Keys like "e.g." — match as literal with whitespace/boundary context
            escaped.append(rf"(?<!\w){ek}(?!\w)")
    pattern = "|".join(escaped)
    return re.compile(pattern)

_PATTERN: re.Pattern | None = _build_pattern(_NORM_DICT)


def normalize_for_tts(text: str) -> str:
    """Replace all known abbreviations/acronyms with spoken equivalents.

    >>> normalize_for_tts("Use the API e.g. for REST calls")
    'Use the A P I for example for rest calls'
    """
    if not _PATTERN or not text:
        return text

    def _replace(match: re.Match) -> str:
        key = match.group(0)
        return _NORM_DICT.get(key, key)

    return _PATTERN.sub(_replace, text)
