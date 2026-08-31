"""Arabic letters inside Hebrew words, and the rule that they are never dropped.

A Hebrew transcript comes back with the occasional Arabic character, sometimes
in the middle of a word. The three cases below are real, from the first Hebrew
run: 3 words out of 475. Nobody in that recording spoke Arabic — Hebrew and
Arabic are both Semitic abjads with letters that map almost one to one, and a
multilingual model slips between the scripts on a sound they share.

The property that matters most here is the one that is easy to get wrong:
**nothing is ever deleted.** `כל` and `שכולו` are ordinary Hebrew words, and
removing them would delete speech that was spoken along with the timestamps
saying where it was — leaving a hole in a beat that nothing downstream can see.
Removing filler is this pipeline's job and it must never remove anything else
by accident.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import pytest  # noqa: E402

from mishne.asr.base import ASRResult, Word  # noqa: E402
from mishne.asr.script import (  # noqa: E402
    has_arabic,
    normalise_hebrew,
    to_hebrew,
)


def _result(words: list[str], language: str = "he") -> ASRResult:
    return ASRResult(
        words=[Word(t, i * 100, i * 100 + 90) for i, t in enumerate(words)],
        language=language, provider="google", model="gemini-3.5-transcribe",
    )


# ── the three real cases ───────────────────────────────────────────────────


@pytest.mark.parametrize("came_back, should_read", [
    # The loanword is Hebrew; ر and ب are not. Transliteration produces the
    # ordinary Hebrew spelling of mashrabiya.
    ("משربייה", "משרבייה"),
    # An entire ordinary Hebrew word in the wrong script.
    ("كل", "כל"),
    # A final ו written as Arabic ه. This one comes out `שכולה` rather than
    # `שכולו` — Arabic's final ه carries a vowel Hebrew writes with either
    # letter and the map cannot know which. One letter out and legible, which
    # is the honest limit of a character map.
    ("שכולه", "שכולה"),
])
def test_the_words_that_actually_came_back(came_back, should_read):
    assert to_hebrew(came_back) == should_read


def test_nothing_is_ever_dropped():
    """The first instinct was to filter these out. `כל` is a word somebody
    said; deleting it loses the speech and the timing of it."""
    result = _result(["ועם", "كل", "זאת"])

    normalise_hebrew(result)

    assert [w.text for w in result.words] == ["ועם", "כל", "זאת"]
    assert all(w.end_ms > w.start_ms for w in result.words)


def test_a_repaired_word_says_so():
    """A wrong-but-plausible Hebrew word is worse to read than an obviously
    wrong Arabic one: the Arabic announces itself and `שכולה` does not."""
    result = _result(["שלום", "كل"])

    assert normalise_hebrew(result) == 1
    assert [w.normalised for w in result.words] == [False, True]


def test_an_arabic_job_is_left_completely_alone():
    """Every one of these characters is correct in Arabic, and converting them
    would turn a good transcript into nonsense."""
    result = _result(["كل", "شيء"], language="ar")

    assert normalise_hebrew(result) == 0
    assert [w.text for w in result.words] == ["كل", "شيء"]


def test_running_it_twice_changes_nothing_the_second_time():
    """The repaired transcript is what gets cached, and the cache is replayed
    (ADR-0008)."""
    result = _result(["كل"])
    assert normalise_hebrew(result) == 1
    assert normalise_hebrew(result) == 0


def test_an_unmapped_arabic_character_survives_visibly():
    """Passing it through keeps it visible, and a visible oddity is a bug
    report. A silently deleted one is a missing word nobody will ever find."""
    assert "؟" in to_hebrew("كل؟")


def test_diacritics_are_dropped_rather_than_invented():
    """Hebrew here is unvocalised, so a short vowel has nothing to become."""
    assert to_hebrew("كَلْ") == "כל"


def test_arabic_digits_become_digits():
    """A number in the wrong digits is unreadable in a transcript and unusable
    in a brief."""
    assert to_hebrew("٢٠٢٦") == "2026"


def test_hebrew_without_arabic_is_untouched():
    result = _result(["שלום", "עולם"])
    assert normalise_hebrew(result) == 0
    assert not has_arabic("שלום")


def test_the_mark_survives_the_cache():
    """`Word.normalised` has to round-trip, or a replayed transcript forgets
    which of its words were repaired and starts claiming it heard them."""
    result = _result(["كل"])
    normalise_hebrew(result)

    back = ASRResult.from_dict(result.to_dict())

    assert back.words[0].text == "כל"
    assert back.words[0].normalised is True
