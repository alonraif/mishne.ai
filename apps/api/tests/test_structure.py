"""Stage 4 is deterministic, so it can be pinned properly.

These tests matter more than they look: structuring is what stage 6 sees, and a
bad beat boundary is invisible in the output but degrades every score
downstream.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from mishne.asr import Word  # noqa: E402
from mishne.pipeline.steps import structure  # noqa: E402


def words(spec: str, start_ms: int = 0, gap_ms: int = 100,
          speaker: str = "A", conf: float = 0.9) -> list[Word]:
    """'hello world' -> two Words, each 300 ms, separated by `gap_ms`."""
    out, t = [], start_ms
    for tok in spec.split():
        out.append(Word(tok, t, t + 300, conf, speaker))
        t += 300 + gap_ms
    return out


def test_sentence_split_on_punctuation():
    beats = structure.build(words("One two. Three four."))
    assert len(beats) == 1, "one speaker, no long pause — one beat"
    assert beats[0].text == "One two. Three four."


def test_beat_split_on_long_pause():
    a = words("The harbour is closing.", start_ms=0)
    b = words("Nobody told us.", start_ms=a[-1].end_ms + 2000)
    beats = structure.build(a + b)
    assert len(beats) == 2, f"expected a split on a 2 s pause, got {len(beats)}"


def test_beat_split_on_speaker_change():
    a = words("What happens next?", speaker="INTERVIEWER")
    b = words("Nobody knows.", start_ms=a[-1].end_ms + 200, speaker="MARGRET")
    beats = structure.build(a + b)
    assert len(beats) == 2
    assert beats[0].speaker == "INTERVIEWER"
    assert beats[1].speaker == "MARGRET"


def test_filler_flagged_not_removed():
    beats = structure.build(words("um uh you know"))
    assert "filler" in beats[0].flags
    # Nothing is deleted at this stage — that is a selection decision.
    assert "um" in beats[0].text


def test_false_start_repeated_prefix():
    beats = structure.build(words("I want to say I want to say something real"))
    assert "false_start" in beats[0].flags


def test_retake_marks_the_earlier_take_superseded():
    first = words("My father kept his boat here for forty one years",
                  start_ms=0)
    second = words("My father kept his boat here for forty three years",
                   start_ms=first[-1].end_ms + 2000)
    beats = structure.build(first + second)
    assert len(beats) == 2
    assert "retake" in beats[1].flags, "later delivery should be the retake"
    assert "superseded" in beats[0].flags, "earlier delivery should be superseded"


def test_retake_not_flagged_across_speakers():
    a = words("The harbour is closing this year", speaker="A")
    b = words("The harbour is closing this year", start_ms=a[-1].end_ms + 2000,
              speaker="B")
    beats = structure.build(a + b)
    assert "retake" not in beats[1].flags, "different speakers is agreement, not a retake"


def test_low_confidence_flagged():
    beats = structure.build(words("the dredging survey came back", conf=0.3))
    assert "low_confidence" in beats[0].flags


def test_quiet_track_warns_but_does_not_flag_every_beat():
    """Regression: a quiet track used to flag every beat off-mic.

    Off-mic is a property of a beat — one speaker turned away, or on the wrong
    microphone. Absolute track loudness cannot detect that, and using it as a
    proxy disqualified the entire transcript and produced an empty cut. A quiet
    track is a gain problem, and the honest response is to say so about the
    track rather than something false about every beat.
    """
    beats = structure.build(words("can you hear me"), loudness_lufs=-52.0)
    assert "off_mic" not in beats[0].flags
    warnings = getattr(structure.build, "warnings", [])
    assert any("quiet" in w.lower() for w in warnings)


def test_empty_input():
    assert structure.build([]) == []


def test_beat_timings_span_their_words():
    ws = words("one two three four")
    beats = structure.build(ws)
    assert beats[0].start_ms == ws[0].start_ms
    assert beats[0].end_ms == ws[-1].end_ms


def test_retake_signal_phrase_flagged():
    beats = structure.build(words("Sorry, can I say that again?"))
    assert "retake_signal" in beats[0].flags


def test_retake_signal_supersedes_the_preceding_take():
    take = words("The harbour closed in March of last year", start_ms=0)
    signal = words("Sorry, let me start again", start_ms=take[-1].end_ms + 2000)
    beats = structure.build(take + signal)
    assert "retake_signal" in beats[1].flags
    assert "superseded" in beats[0].flags


def test_ordinary_question_is_not_a_retake_signal():
    beats = structure.build(words("Can I ask what happens to the boats?"))
    assert "retake_signal" not in beats[0].flags
