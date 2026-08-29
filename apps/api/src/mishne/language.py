"""Language and script handling.

Hebrew is a first-class target, not an afterthought, and it breaks assumptions
that English-only code makes silently:

- **No capitalisation.** Any heuristic keying on capital letters — sentence
  starts, proper nouns, acronyms — returns nothing and fails quietly.
- **Different filler words**, and different retake phrasing.
- **Right-to-left**, with left-to-right runs inside it. A Hebrew transcript
  routinely contains Latin product names, numbers and timecode, and each of
  those runs LTR inside an RTL paragraph. Getting this wrong is not cosmetic;
  a timecode rendered backwards is unusable.
- **Whisper needs a bigger model for Hebrew than for English.** `base` is
  usable for English and poor for Hebrew; `medium` or `large-v3` is the
  realistic floor. This is a cost and latency fact, not a preference.
"""

from __future__ import annotations

import re

# Scripts written right to left that we might plausibly see.
RTL_RANGES = (
    (0x0590, 0x05FF),   # Hebrew
    (0x0600, 0x06FF),   # Arabic
    (0x0700, 0x074F),   # Syriac
    (0x0750, 0x077F),   # Arabic Supplement
    (0x08A0, 0x08FF),   # Arabic Extended-A
    (0xFB1D, 0xFDFF),   # Hebrew/Arabic presentation forms
    (0xFE70, 0xFEFF),   # Arabic presentation forms-B
)

RTL_LANGUAGES = {"he", "iw", "ar", "fa", "ur", "yi", "he-IL", "ar-SA"}

# Whisper model sizes below this are not worth running on Hebrew.
MIN_MODEL_FOR_RTL = {"medium", "large", "large-v2", "large-v3", "turbo"}


def is_rtl_char(ch: str) -> bool:
    cp = ord(ch)
    return any(lo <= cp <= hi for lo, hi in RTL_RANGES)


def rtl_ratio(text: str) -> float:
    """Fraction of letters that are RTL. Digits and punctuation are neutral."""
    letters = [c for c in text if c.isalpha()]
    if not letters:
        return 0.0
    return sum(1 for c in letters if is_rtl_char(c)) / len(letters)


def is_rtl_text(text: str, threshold: float = 0.3) -> bool:
    """True when a string should be laid out right to left.

    A threshold rather than a majority: a Hebrew sentence quoting an English
    phrase is still a Hebrew sentence, and should not flip direction because the
    quote happened to be long.
    """
    return rtl_ratio(text) >= threshold


def is_rtl_language(code: str | None) -> bool:
    if not code:
        return False
    return code.split("-")[0].lower() in {c.split("-")[0] for c in RTL_LANGUAGES}


def direction(text: str = "", language: str | None = None) -> str:
    """'rtl' or 'ltr'. Language wins when known; otherwise inspect the text."""
    if language:
        return "rtl" if is_rtl_language(language) else "ltr"
    return "rtl" if is_rtl_text(text) else "ltr"


def warn_model_for_language(model: str, language: str | None) -> str | None:
    """Whisper size warning for RTL languages. Returns a message or None.

    Matches the size *within* the name rather than comparing the whole thing.
    A model is just as often given as a path — `models/faster-whisper-large-v3`
    — as by bare size, and comparing basenames warned that large-v3 was too
    small, which is exactly the advice someone who already fixed the problem
    does not need.
    """
    if not is_rtl_language(language):
        return None
    name = model.replace("\\", "/").split("/")[-1].lower()
    if any(size in name for size in MIN_MODEL_FOR_RTL):
        return None
    return (
        f"Whisper '{name}' is small for {language}. Hebrew and Arabic degrade "
        f"sharply below 'medium': word timestamps drift and filler gets "
        f"dropped, which matters here because removing filler is this system's "
        f"job and it cannot do it if the ASR already did. Use medium or "
        f"large-v3."
    )
