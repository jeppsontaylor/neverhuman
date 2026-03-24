"""
pipeline/output_sanitizer.py — Post-LLM output sanitizer

Cleans up LLM output before it reaches TTS or the chat UI.
Two concerns:
  1. Identity leaks: Qwen3.5 ignores system prompt identity overrides.
     We catch and replace known identity phrases at the text level.
  2. Markdown formatting: The model outputs bold, headers, numbered lists,
     bullet points despite system prompt rules. We strip them for TTS.

Design:
  - Compiled regex patterns for O(1) matching
  - Runs on each token/sentence before display and TTS
  - Idempotent: safe to run multiple times on the same text
"""

import re
import logging

log = logging.getLogger("gary.output_sanitizer")

# ── Identity replacement rules ──────────────────────────────────────────────
# Compiled once, applied per-sentence. Order matters: longer patterns first.

_IDENTITY_RULES: list[tuple[re.Pattern, str]] = [
    # "I am Qwen3.5" / "I'm Qwen3.5" / "I am Qwen 3" etc.
    (re.compile(r"\bI(?:'m| am)\s+Qwen(?:\s*\d+(?:\.\d+)?)?", re.IGNORECASE), "I'm GARY"),
    # "My name is Qwen" / "My name is Qwen3.5"
    (re.compile(r"\bmy name is\s+Qwen(?:\s*\d+(?:\.\d+)?)?", re.IGNORECASE), "my name is GARY"),
    # "developed by Tongyi Lab" / "created by Tongyi Lab" / "built by Tongyi Lab"
    (re.compile(r"\b(?:developed|created|built|made|designed)\s+by\s+(?:Tongyi\s*Lab|Alibaba(?:\s+Cloud)?|Alibaba\s+Group)", re.IGNORECASE),
     "built by the NeverHuman team"),
    # "Tongyi Lab" standalone
    (re.compile(r"\bTongyi\s*Lab\b", re.IGNORECASE), "the NeverHuman team"),
    # "Alibaba Cloud" / "Alibaba Group" standalone
    (re.compile(r"\bAlibaba(?:\s+Cloud|\s+Group)?\b", re.IGNORECASE), "NeverHuman"),
    # "as Qwen" / "called Qwen"
    (re.compile(r"\b(?:as|called|named)\s+Qwen(?:\s*\d+(?:\.\d+)?)?", re.IGNORECASE), "as GARY"),
    # Bare "Qwen3.5" or "Qwen 3.5" or "Qwen3" used as self-reference
    (re.compile(r"\bQwen\s*3(?:\.5)?\b", re.IGNORECASE), "GARY"),
    # Bare "Qwen" used as a name
    (re.compile(r"\bQwen\b"), "GARY"),
]

# ── Markdown stripping rules ───────────────────────────────────────────────
# Applied to individual tokens as they stream AND to complete sentences.

# Headers: ### Header Text → Header Text
_MD_HEADER = re.compile(r'^\s*#{1,6}\s+', re.MULTILINE)
# Bold: **text** or __text__ → text
_MD_BOLD = re.compile(r'\*\*(.+?)\*\*|__(.+?)__')
# Italic: *text* or _text_ → text  (single asterisk/underscore)
_MD_ITALIC = re.compile(r'(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)|(?<!_)_(?!_)(.+?)(?<!_)_(?!_)')
# Bullet points: - text or * text at line start
_MD_BULLET = re.compile(r'^\s*[-*•]\s+', re.MULTILINE)
# Numbered lists: 1. text or 1) text at line start
_MD_NUMBERED = re.compile(r'^\s*\d+[.)]\s+', re.MULTILINE)
# Backtick code: `code` → code
_MD_CODE = re.compile(r'`([^`]+)`')
# Code blocks: ```lang\ncode\n``` → code
_MD_CODEBLOCK = re.compile(r'```\w*\n?(.*?)```', re.DOTALL)
# Links: [text](url) → text
_MD_LINK = re.compile(r'\[([^\]]+)\]\([^)]+\)')


def sanitize_identity(text: str) -> str:
    """Replace all known Qwen identity leaks with GARY equivalents."""
    for pattern, replacement in _IDENTITY_RULES:
        text = pattern.sub(replacement, text)
    return text


def strip_markdown(text: str) -> str:
    """Remove all markdown formatting from text for clean TTS output."""
    text = _MD_CODEBLOCK.sub(r'\1', text)
    text = _MD_HEADER.sub('', text)
    text = _MD_BOLD.sub(lambda m: m.group(1) or m.group(2), text)
    text = _MD_ITALIC.sub(lambda m: m.group(1) or m.group(2), text)
    text = _MD_BULLET.sub('', text)
    text = _MD_NUMBERED.sub('', text)
    text = _MD_CODE.sub(r'\1', text)
    text = _MD_LINK.sub(r'\1', text)
    return text


def sanitize_token(token: str) -> str:
    """Light sanitization for individual streaming tokens.

    Only strips markdown characters that appear in isolation
    (e.g. '**', '###'). Identity replacement is too fragile
    at the token level — it's done on complete sentences.
    """
    # Strip isolated markdown markers
    if token.strip() in ('**', '***', '##', '###', '####', '#####', '######', '-', '*', '•'):
        return ''
    # Strip leading # from header tokens
    if token.startswith('#'):
        return token.lstrip('#').lstrip()
    # Strip ** from bold markers mid-token
    return token.replace('**', '').replace('__', '')


def sanitize_sentence(sentence: str) -> str:
    """Full sanitization for complete sentences before TTS.

    Applies identity replacement + markdown stripping.
    """
    sentence = sanitize_identity(sentence)
    sentence = strip_markdown(sentence)
    # Clean up any double spaces left behind
    sentence = re.sub(r'  +', ' ', sentence).strip()
    return sentence
