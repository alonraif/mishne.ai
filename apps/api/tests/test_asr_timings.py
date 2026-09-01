"""One impossible word timestamp, and everything it broke downstream.

From a real run: a 46-minute two-mic Hebrew podcast came back from the browser
with a speaker legend reading "Chen · 49m 51s" and a single 23-minute beat.
Neither is a rounding error and neither was a bug in the code that reported it —
`steps/speakers` totals word durations and `steps/structure` gives a beat the
span of its words, and both were reporting a vendor timestamp faithfully.

The culprit was one word out of 6,812: `start=100, end=1_412_600`, sitting in the
middle of the transcript where it was spoken. It accounted for 23.5 of the 25
minutes of over-reported talking, and for the whole of the 23-minute beat. The
median word length in that file is 300 ms before and after the repair, which is
the number that says this is a clamp on the broken and not a tax on the rest.

What these assert is the invariant in `asr/timings.py`, and above all the
decision underneath it: **the word order is never changed.** Sorting by start
time would have moved that word's text to the top of a delivered transcript.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from mishne.asr.base import ASRResult, Word  # noqa: E402
from mishne.asr.timings import MAX_WORD_MS, sanitise  # noqa: E402


def _result(spans, *, audio_seconds: float = 0.0) -> ASRResult:
    return ASRResult(
        words=[Word(f"w{i}", s, e) for i, (s, e) in enumerate(spans)],
        language="he", provider="test", model="test",
        audio_seconds=audio_seconds,
    )


def _spans(result: ASRResult) -> list[tuple[int, int]]:
    return [(w.start_ms, w.end_ms) for w in result.words]


def _ok(result: ASRResult, *, audio_ms: int | None = None) -> None:
    """The whole invariant, asserted in one place."""
    words = result.words
    for a, b in zip(words, words[1:]):
        assert a.start_ms <= b.start_ms, "starts must never decrease"
        # Past the next word only by the millisecond it needs to exist, which is
        # reachable only where unusable timestamps share a gap with no room.
        assert a.end_ms <= max(b.start_ms, a.start_ms + 1), (
            "a word must not run past the next one"
        )
    for w in words:
        assert w.end_ms > w.start_ms, "every span is positive"
        assert w.end_ms - w.start_ms <= MAX_WORD_MS
        assert w.start_ms >= 0
        if audio_ms is not None:
            assert w.end_ms <= audio_ms, "nothing falls outside the audio"


def test_the_real_case_one_word_spanning_the_whole_file():
    """The word that produced the 23-minute beat, in the shape it arrived in.

    Its start is 23 minutes too early and its end is 23 minutes too late, and it
    sits among sound words on both sides — as it did in the file, at word 2,530
    of 6,812. So it belongs in the gap between its neighbours, which is exactly
    where it was spoken.
    """
    sound = [(1_380_000 + 500 * i, 1_380_400 + 500 * i) for i in range(8)]
    spans = sound[:4] + [(100, 1_412_600)] + sound[4:]
    r = _result(spans, audio_seconds=2760.5)

    # Two words move: the broken one, and the word before it, whose end no
    # longer runs past where the broken one now starts. Nothing else.
    assert sanitise(r) == 2
    _ok(r, audio_ms=2_760_500)
    bad = r.words[4]
    assert 1_381_500 <= bad.start_ms <= 1_382_000, "placed between its neighbours"
    assert bad.end_ms <= 1_382_000
    # Every sound word keeps its own timing, except that the one just before the
    # repair no longer runs past it — which is the rule doing its job.
    assert [w.start_ms for w in r.words if w.text != "w4"] == [s for s, _ in sound]
    assert _spans(r)[:3] == sound[:3] and _spans(r)[5:] == sound[4:]
    assert r.words[3].end_ms <= bad.start_ms
    assert r.words[-1].end_ms - r.words[0].start_ms < 4000


def test_the_word_order_is_never_changed():
    """The property that rules out sorting by start time.

    The words are the transcript; the timestamps are metadata on them. A word
    whose start says 100 ms in the middle of a 46-minute file has a broken
    timestamp, not a broken position, and moving it would put its text at the
    top of the page.
    """
    spans = [(1000, 1400), (2000, 2400), (3000, 3400),
             (100, 200),                      # the broken one, in the middle
             (4000, 4400), (5000, 5400), (6000, 6400)]
    r = _result(spans, audio_seconds=7.0)
    sanitise(r)
    assert [w.text for w in r.words] == [f"w{i}" for i in range(7)]
    _ok(r, audio_ms=7000)
    assert r.words[3].start_ms >= 3000, "repaired forward, not reordered"
    assert r.words[3].text == "w3", "and still the fourth word of the transcript"


def test_talk_time_stops_exceeding_the_recording():
    """The legend's "49m 51s on a 46-minute file" read through `steps/speakers`:
    it sums `duration_ms` per voice, and a sum over spans that overlap is not a
    duration of anything."""
    r = _result([(0, 60_000), (1000, 2000), (2000, 3000)], audio_seconds=3.0)
    sanitise(r)
    assert sum(w.duration_ms for w in r.words) <= 3000
    _ok(r, audio_ms=3000)


def test_no_word_is_ever_dropped():
    """A word with an unusable timestamp is still a word somebody said.

    Dropping it would leave a hole in the transcript that nothing downstream can
    see — the same property `asr/script.py` protects for the same reason.
    """
    r = _result([(500, 400), (500, 500), (10, 88_000_000)], audio_seconds=1.0)
    sanitise(r)
    assert [w.text for w in r.words] == ["w0", "w1", "w2"]
    _ok(r, audio_ms=1000)


def test_a_run_of_broken_words_is_spread_through_the_gap():
    """Not stacked on one millisecond: three unusable timestamps between two
    sound ones are still three words in order, and the gap between the words
    that survived is what is known about where they were."""
    sound = [(1000 * i, 1000 * i + 400) for i in range(1, 9)]
    spans = sound[:4] + [(0, 5), (0, 5), (0, 5)] + sound[4:]
    r = _result(spans, audio_seconds=9.0)
    sanitise(r)
    _ok(r, audio_ms=9000)

    middle = [w.start_ms for w in r.words[4:7]]
    assert middle == sorted(middle) and len(set(middle)) == 3, "spread, not stacked"
    assert all(4000 <= s <= 5000 for s in middle), "inside the gap they fell in"
    assert _spans(r)[:3] == sound[:3], "the sound words either side are untouched"
    assert _spans(r)[7:] == sound[4:]
    # The one just before the run gives up only the overlap with it.
    assert r.words[3].start_ms == sound[3][0]
    assert r.words[3].end_ms <= r.words[4].start_ms


def test_the_last_word_has_no_next_word_and_is_bounded_anyway():
    """Nothing follows it, so the only bounds are the audio and MAX_WORD_MS."""
    unbounded = _result([(1000, 1400), (1500, 88_000_000)])
    sanitise(unbounded)
    assert unbounded.words[-1].end_ms == 1500 + MAX_WORD_MS

    measured = _result([(1000, 1400), (1500, 88_000_000)], audio_seconds=2.0)
    sanitise(measured)
    assert measured.words[-1].end_ms == 2000


def test_good_timings_are_left_exactly_alone():
    """The repair must not be a tax on every transcript that was already right —
    including a word held for a full second and words that touch nose to tail."""
    spans = [(0, 400), (400, 1400), (1400, 1600)]
    r = _result(spans, audio_seconds=1.6)
    assert sanitise(r) == 0
    assert _spans(r) == spans


def test_a_second_pass_changes_nothing():
    """It runs on a fresh transcription and again on every replay of the cached
    one (`steps/transcribe`), so idempotence is the contract, not a nicety."""
    r = _result([(500, 400), (1500, 88_000_000), (10, 20_000), (1600, 1700)],
                audio_seconds=100.0)
    sanitise(r)
    before = _spans(r)
    assert sanitise(r) == 0
    assert _spans(r) == before
    _ok(r, audio_ms=100_000)
