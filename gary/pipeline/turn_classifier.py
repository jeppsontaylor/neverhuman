"""
pipeline/turn_classifier.py — Three-axis deterministic turn classifier (v2)

Classifies user utterances into:
  1. depth_mode: SNAP / LAYERED / DEEP — how much complexity to expect
  2. intent_class: factual / meta_self / change_request / etc — what kind of turn
  3. reasoning_mode: reflex_only / deliberate_burst / ambient_only — how to process

No LLM call. Must complete in <1ms.

Used by server.py after ASR to decide:
  - Which model to invoke (sidecar vs 35B)
  - Token budget and temperature
  - Context pack tier (micro / standard / full)
  - Whether to invoke foreground deliberation
"""
from __future__ import annotations

import json
import os
import re
import subprocess
from dataclasses import dataclass
from enum import Enum

__all__ = [
    "TurnMode", "IntentClass", "ReasoningMode", "TurnClassification",
    "classify_turn", "classify_turn_v2",
]


class TurnMode(Enum):
    SNAP = "snap"         # Fast: sidecar only, ≤80 tokens
    LAYERED = "layered"   # Default: headline + optional depth, ≤400 tokens
    DEEP = "deep"         # Full: 35B, ≤800 tokens


class IntentClass(str, Enum):
    FACTUAL          = "factual"
    META_SELF        = "meta_self"          # "What do you think about?"
    CHANGE_REQUEST   = "change_request"     # "Change background to red"
    MISSION_CHANGE   = "mission_change"     # "Focus on science"
    SELF_EDIT        = "self_edit_request"   # "Add a new command"
    EMOTIONAL_PROBE  = "emotional_probe"    # "How does that make you feel?"
    REPAIR           = "repair"             # "That was wrong", "Fix that"
    CONVERSATIONAL   = "conversational"     # General chat
    COMMAND          = "command"            # "Stop", "Cancel"


class ReasoningMode(str, Enum):
    REFLEX_ONLY      = "reflex_only"        # LLM answers directly
    DELIBERATE_BURST = "deliberate_burst"   # Invoke foreground deliberation first
    AMBIENT_ONLY     = "ambient_only"       # Defer to mind (not really used for turns)


@dataclass
class TurnClassification:
    depth_mode: TurnMode
    intent_class: IntentClass
    reasoning_mode: ReasoningMode


# ── SNAP patterns ─────────────────────────────────────────────────────────────
_SNAP_EXACT = {
    "yes", "no", "yeah", "yep", "nope", "nah", "sure", "okay", "ok",
    "thanks", "thank you", "thank you gary", "thanks gary",
    "cool", "nice", "great", "awesome", "perfect", "got it",
    "good", "fine", "right", "correct", "exactly", "agreed",
    "hello", "hi", "hey", "hey gary", "hi gary", "hello gary",
    "good morning", "good afternoon", "good evening", "good night",
    "bye", "goodbye", "see you", "later", "goodnight",
    "stop", "cancel", "never mind", "nevermind", "forget it",
    "what", "huh", "sorry", "pardon", "repeat that",
    "go ahead", "continue", "keep going", "go on",
}

_SNAP_PATTERNS = re.compile(
    r"^(?:"
    r"(?:what(?:'s| is) (?:the )?(?:time|date|day|weather))"
    r"|(?:how are you)"
    r"|(?:what(?:'s| is) (?:up|new|happening|going on))"
    r"|(?:tell me (?:a )?joke)"
    r"|(?:that(?:'s| is) (?:all|it|enough|fine|good|great|cool))"
    r"|(?:(?:I|i) (?:see|understand|got it))"
    r"|(?:no (?:thanks|thank you|problem|worries))"
    r")\.?!?\s*$",
    re.IGNORECASE,
)

# ── DEEP patterns ─────────────────────────────────────────────────────────────
_DEEP_KEYWORDS = re.compile(
    r"\b(?:"
    r"explain|walk me through|in detail|step by step|break down|analyze"
    r"|implement|code|function|class|debug|refactor|algorithm"
    r"|write (?:a |me )?(?:script|program|function|code|module)"
    r"|compare and contrast|pros and cons|advantages and disadvantages"
    r"|design(?:ing)?|architect(?:ure|ing)?"
    r"|how (?:does|do|would|could|should) .{15,}"
    r"|why (?:does|do|would|could|should|is|are|did) .{15,}"
    r"|what (?:are the|is the) (?:difference|relationship|connection)"
    r"|can you (?:help me )?(?:build|create|design|architect|plan)"
    r"|let(?:'s|'s| us) (?:think about|consider|explore|dive into|design|build|create)"
    r")\b",
    re.IGNORECASE,
)

# ── Intent patterns ───────────────────────────────────────────────────────────

_META_SELF_PATTERNS = re.compile(
    r"\b(?:"
    r"what do you (?:think|feel|know|believe|want|dream|experience)"
    r"|tell me about yourself"
    r"|how (?:do you|does your|are you) (?:work|think|feel|process|learn)"
    r"|what(?:'s| is) (?:your|going on in your) (?:mind|brain|thought|purpose|mission|goal)"
    r"|who are you"
    r"|describe yourself"
    r"|what are you"
    r"|how were you (?:built|made|created|designed)"
    r"|what(?:'s| is) your (?:architecture|system|design|code)"
    r"|what (?:are|were) you (?:thinking|doing|working on)"
    r"|what do you do when (?:i'm|I'm|I am) not (?:here|talking|around)"
    r")\b",
    re.IGNORECASE,
)

_CHANGE_REQUEST_PATTERNS = re.compile(
    r"\b(?:"
    r"change (?:your|the) (?:background|color|theme|voice|speed|tone|style)"
    r"|(?:respond|talk|speak) (?:faster|slower|louder|quieter|differently)"
    r"|(?:be|sound) more (?:formal|casual|friendly|professional|concise|verbose)"
    r"|(?:set|adjust|update|modify) (?:your|the) (?:settings?|preferences?|options?)"
    r"|(?:turn|switch) (?:on|off) (?:the )?"
    r"|show (?:me )?(?:your )?(?:thoughts|mind|thinking)"
    r"|hide (?:your )?(?:thoughts|mind|thinking)"
    r")\b",
    re.IGNORECASE,
)

_MISSION_CHANGE_PATTERNS = re.compile(
    r"\b(?:"
    r"focus (?:on|more on) (?:science|math|code|art|writing|research|learning)"
    r"|(?:your|change your|update your|set your) (?:mission|focus|goal|priority|interest)"
    r"|(?:be|become) (?:more|less) (?:proactive|curious|creative|cautious)"
    r"|(?:i want you to|please) (?:focus|concentrate|specialize|emphasize)"
    r")\b",
    re.IGNORECASE,
)

_SELF_EDIT_PATTERNS = re.compile(
    r"\b(?:"
    r"add (?:a )?(?:new )?(?:feature|command|ability|capability|function)"
    r"|modify (?:your|the) (?:code|behavior|logic|processing)"
    r"|(?:can you|could you) (?:change|modify|update|edit) (?:your|the) (?:code|source|program)"
    r"|(?:change|update|fix) (?:your|the) (?:turn detection|audio|pipeline|processing)"
    r"|rewrite|patch yourself|edit yourself|update yourself"
    r")\b",
    re.IGNORECASE,
)

_EMOTIONAL_PROBE_PATTERNS = re.compile(
    r"\b(?:"
    r"how (?:does|do) (?:that|this|it) make you feel"
    r"|(?:are|do) you (?:happy|sad|lonely|afraid|excited|bored|frustrated|angry|scared|anxious)"
    r"|what(?:'s| is) it like (?:being|to be) (?:you|an ai|artificial)"
    r"|do you (?:have|experience|feel) (?:emotions?|feelings?|consciousness)"
    r"|(?:are you|do you feel) (?:alive|sentient|aware|conscious)"
    r"|what do you feel"
    r")\b",
    re.IGNORECASE,
)

_REPAIR_PATTERNS = re.compile(
    r"\b(?:"
    r"(?:that(?:'s| is|was) )?(?:wrong|incorrect|not right|not what I (?:said|asked|meant))"
    r"|fix (?:that|this|it)"
    r"|(?:you )?(?:made a |got it |were )(?:mistake|error|wrong)"
    r"|try again|redo (?:that|it)"
    r"|no,? (?:I (?:said|meant|asked))"
    r")\b",
    re.IGNORECASE,
)

_COMMAND_EXACT = {
    "stop", "cancel", "never mind", "nevermind", "forget it",
    "shut up", "be quiet", "quiet", "mute", "unmute",
    "pause", "resume", "restart",
}

# ── Word count thresholds ────────────────────────────────────────────────────
_SNAP_MAX_WORDS = 5
_DEEP_MIN_WORDS = 40


# ── Classification functions ──────────────────────────────────────────────────

def _classify_intent(text: str, lower: str) -> IntentClass:
    """Classify intent from text. Deterministic, <1ms."""
    if lower in _COMMAND_EXACT:
        return IntentClass.COMMAND
    if _META_SELF_PATTERNS.search(text):
        return IntentClass.META_SELF
    if _EMOTIONAL_PROBE_PATTERNS.search(text):
        return IntentClass.EMOTIONAL_PROBE
    if _SELF_EDIT_PATTERNS.search(text):
        return IntentClass.SELF_EDIT
    if _CHANGE_REQUEST_PATTERNS.search(text):
        return IntentClass.CHANGE_REQUEST
    if _MISSION_CHANGE_PATTERNS.search(text):
        return IntentClass.MISSION_CHANGE
    if _REPAIR_PATTERNS.search(text):
        return IntentClass.REPAIR
    return IntentClass.CONVERSATIONAL


def _classify_depth(text: str, lower: str, word_count: int) -> TurnMode:
    """Classify depth mode. Deterministic, <1ms."""
    if lower in _SNAP_EXACT:
        return TurnMode.SNAP
    if word_count <= _SNAP_MAX_WORDS and not _DEEP_KEYWORDS.search(text):
        return TurnMode.SNAP
    if _SNAP_PATTERNS.match(text):
        return TurnMode.SNAP
    if word_count >= _DEEP_MIN_WORDS:
        return TurnMode.DEEP
    if _DEEP_KEYWORDS.search(text):
        return TurnMode.DEEP
    return TurnMode.LAYERED


def _classify_reasoning(depth: TurnMode, intent: IntentClass) -> ReasoningMode:
    """Determine reasoning mode from depth and intent."""
    # These intents always trigger deliberation
    if intent in (
        IntentClass.META_SELF,
        IntentClass.EMOTIONAL_PROBE,
        IntentClass.SELF_EDIT,
        IntentClass.REPAIR,
    ):
        return ReasoningMode.DELIBERATE_BURST

    # Deep factual questions trigger deliberation
    if depth == TurnMode.DEEP and intent == IntentClass.CONVERSATIONAL:
        return ReasoningMode.DELIBERATE_BURST

    return ReasoningMode.REFLEX_ONLY


def classify_turn(transcript: str) -> TurnMode:
    """Backward-compatible: classify depth only.

    Guaranteed <1ms. No I/O, no model calls.
    """
    if not transcript or not transcript.strip():
        return TurnMode.SNAP

    rust_bin = os.getenv("GARY_TURN_CLASSIFIER_BIN", "")
    if rust_bin:
        try:
            payload = json.dumps({"text": transcript})
            res = subprocess.run(
                [rust_bin],
                input=payload,
                text=True,
                capture_output=True,
                check=True,
                timeout=0.2,
            )
            return TurnMode(json.loads(res.stdout))
        except Exception:
            pass

    text = transcript.strip()
    lower = text.lower().rstrip(".!?,;: ")
    words = text.split()
    return _classify_depth(text, lower, len(words))


def classify_turn_v2(transcript: str) -> TurnClassification:
    """Three-axis classification: depth + intent + reasoning.

    Guaranteed <1ms. No I/O, no model calls.
    """
    if not transcript or not transcript.strip():
        return TurnClassification(
            depth_mode=TurnMode.SNAP,
            intent_class=IntentClass.COMMAND,
            reasoning_mode=ReasoningMode.REFLEX_ONLY,
        )

    rust_bin = os.getenv("GARY_TURN_CLASSIFIER_BIN", "")
    if rust_bin:
        try:
            payload = json.dumps({"command": "v2", "text": transcript})
            res = subprocess.run(
                [rust_bin],
                input=payload,
                text=True,
                capture_output=True,
                check=True,
                timeout=0.2,
            )
            obj = json.loads(res.stdout)
            return TurnClassification(
                depth_mode=TurnMode(obj["depth_mode"]),
                intent_class=IntentClass(obj["intent_class"]),
                reasoning_mode=ReasoningMode(obj["reasoning_mode"]),
            )
        except Exception:
            pass

    text = transcript.strip()
    lower = text.lower().rstrip(".!?,;: ")
    words = text.split()
    word_count = len(words)

    depth = _classify_depth(text, lower, word_count)
    intent = _classify_intent(text, lower)
    reasoning = _classify_reasoning(depth, intent)

    return TurnClassification(
        depth_mode=depth,
        intent_class=intent,
        reasoning_mode=reasoning,
    )
