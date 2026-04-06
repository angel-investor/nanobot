"""Shared voice utilities for JARVIS."""

from __future__ import annotations

# Known Whisper hallucination patterns (lower-cased substrings).
# Whisper produces these when fed silent or very quiet audio.
_HALLUCINATION_FRAGMENTS = [
    "amara.org",
    "字幕由",
    "subtitles by",
    "thank you for watching",
    "请订阅",
    "by bwd",
    "transcript by",
    "transcribed by",
    "caption",
    "sub by",
    "字幕制作",
    "翻译",
    "视频来自",
]


def is_hallucination(text: str) -> bool:
    """Return True if *text* looks like a Whisper hallucination rather than real speech."""
    t = text.lower().strip()
    if len(t) < 2:
        return True
    return any(frag in t for frag in _HALLUCINATION_FRAGMENTS)
