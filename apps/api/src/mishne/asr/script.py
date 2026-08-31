"""Arabic letters that arrive inside Hebrew words, and what to do about them.

## What is actually happening

A Hebrew transcript comes back with the occasional Arabic character, sometimes
mid-word. Three real examples out of 475 words on the first Hebrew run:

    משربייה     mashrabiya — the loanword is Hebrew, ر and ب are not
    كل          "all" — an ordinary Hebrew word (כל) written entirely in Arabic
    שכולه       "that all of it" — the final ו written as Arabic ه

Nobody in that recording said a word of Arabic. Hebrew and Arabic are both
Semitic abjads with letters that map almost one to one, and a multilingual model
slips between the two scripts on a sound they share. It is a spelling artefact,
not a language detection failure.

## Why the words are converted rather than dropped

Dropping them was the first instinct and it is the wrong one. `כל` and `שכולו`
are ordinary Hebrew words: removing them deletes speech that was spoken, along
with the timestamps that say where it was — and a beat assembled from the
remaining words has a hole in it that nothing downstream can see. The pipeline
removes filler on purpose and must never remove anything else by accident.

Transliteration keeps the word, its timing and its speaker, and puts it in the
script the rest of the sentence is in. On the three cases above it produces
`משרבייה` and `כל` exactly, and `שכולה` for `שכולו` — one letter out, because
Arabic's final ه carries a vowel Hebrew writes with either ה or ו and this map
cannot know which. That is the honest limit of it.

## And why every converted word is marked

A wrong-but-plausible Hebrew word is worse to read than an obviously-wrong
Arabic one: the Arabic announces itself, and `שכולה` does not. So `Word.
normalised` is set on anything this touches, the count is logged, and the
transcript keeps the ability to show which words were repaired rather than
heard. Silent correction is the failure mode to avoid; visible correction is
the point.

## Hebrew only

Applied when the transcript's language is Hebrew and never otherwise. On an
Arabic job every one of these characters is correct, and converting them would
turn a good transcript into nonsense.
"""

from __future__ import annotations

#: Arabic letter to its Hebrew homologue. Consonants map by sound; the six
#: Arabic consonants Hebrew has no letter for take a geresh, which is how
#: Hebrew has always written them (ג' for the j in "jeans").
ARABIC_TO_HEBREW = {
    "ا": "א", "أ": "א", "إ": "א", "آ": "א", "ء": "א", "ئ": "י", "ؤ": "ו",
    "ب": "ב", "ت": "ת", "ث": "ת'", "ج": "ג'", "ح": "ח", "خ": "כ'",
    "د": "ד", "ذ": "ד'", "ر": "ר", "ز": "ז", "س": "ס", "ش": "ש",
    "ص": "צ", "ض": "צ'", "ط": "ט", "ظ": "ט'", "ع": "ע", "غ": "ע'",
    "ف": "פ", "ق": "ק", "ك": "כ", "ل": "ל", "م": "מ", "ن": "נ",
    "ه": "ה", "ة": "ה", "و": "ו", "ي": "י", "ى": "י",
    # Arabic-Indic digits. A number in the wrong digits is unreadable in a
    # transcript and unusable in a brief.
    "٠": "0", "١": "1", "٢": "2", "٣": "3", "٤": "4",
    "٥": "5", "٦": "6", "٧": "7", "٨": "8", "٩": "9",
}

#: Short vowels and other diacritics. Hebrew text here is unvocalised, so these
#: are dropped rather than transliterated into something nobody writes.
_DIACRITICS = {chr(c) for c in range(0x064B, 0x0653)} | {"ـ", "ٰ"}

_RANGES = (
    (0x0600, 0x06FF), (0x0750, 0x077F), (0x08A0, 0x08FF),
    (0xFB50, 0xFDFF), (0xFE70, 0xFEFF),
)


def is_arabic(ch: str) -> bool:
    cp = ord(ch)
    return any(lo <= cp <= hi for lo, hi in _RANGES)


def has_arabic(text: str) -> bool:
    return any(is_arabic(c) for c in text)


def to_hebrew(text: str) -> str:
    """Every Arabic character replaced by its Hebrew homologue.

    An Arabic character with no mapping is left exactly as it is. Passing it
    through unchanged keeps it visible, and a visible oddity is a bug report;
    a silently deleted one is a missing word nobody will ever find.
    """
    out = []
    for ch in text:
        if ch in _DIACRITICS:
            continue
        out.append(ARABIC_TO_HEBREW.get(ch, ch))
    return "".join(out)


def normalise_hebrew(result) -> int:
    """Repair Arabic-script words in a Hebrew transcript. Returns how many.

    Mutates in place and marks what it touched. Idempotent: a transcript that
    has already been through this has no Arabic left to convert, which matters
    because the cached `.asr.json` is replayed (ADR-0008).
    """
    if (result.language or "").split("-")[0].lower() != "he":
        return 0
    changed = 0
    for word in result.words:
        if not has_arabic(word.text):
            continue
        repaired = to_hebrew(word.text)
        if repaired != word.text:
            word.text = repaired
            word.normalised = True
            changed += 1
    return changed
