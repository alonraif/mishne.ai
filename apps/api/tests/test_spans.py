"""Candidate spans: cutting inside a beat, and the silence that has to be there.

The failure this guards against is a cut that sounds wrong. Everything here is
about a boundary being physically possible before it is allowed to be
editorially interesting — a span whose endpoints have no silence behind them
clips the speech, and no rationale from a model makes it audible.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from mishne.asr import Word  # noqa: E402
from mishne.pipeline.steps import propose, select  # noqa: E402
from mishne.pipeline.steps.structure import Beat  # noqa: E402
from mishne.pipeline.steps.vad import SpeechMap  # noqa: E402


def beat_of(n_words: int, word_ms: int = 500, gaps: dict | None = None) -> Beat:
    """A beat of `n_words`, with explicit silence after chosen word indices."""
    gaps = gaps or {}
    words, t = [], 0
    for i in range(n_words):
        words.append(Word(text=f"w{i}", start_ms=t, end_ms=t + word_ms))
        t += word_ms + gaps.get(i, 0)
    return Beat(id="beat_0000", idx=0, speaker="S1",
                start_ms=words[0].start_ms, end_ms=words[-1].end_ms,
                text=" ".join(w.text for w in words), words=words)


def speech_from(b: Beat) -> SpeechMap:
    """The VAD map implied by a beat's word timings."""
    seg, cur = [], [b.words[0].start_ms, b.words[0].end_ms]
    for w in b.words[1:]:
        if w.start_ms > cur[1]:
            seg.append(tuple(cur))
            cur = [w.start_ms, w.end_ms]
        else:
            cur[1] = w.end_ms
    seg.append(tuple(cur))
    return SpeechMap(speech=seg, duration_ms=b.end_ms + 1000)


# --- the gate ----------------------------------------------------------------


def test_only_real_silence_is_a_legal_cut_point():
    b = beat_of(30, gaps={9: 800, 19: 500})
    pts = propose.cut_points(b, speech_from(b))
    # The two silences, plus the beat's own edges, and nothing else.
    assert pts == [0, 10, 20, 30]


def test_silence_below_the_floor_is_not_a_cut_point():
    """200 ms is not enough for six frames of handle at either side."""
    b = beat_of(30, gaps={9: 200, 19: 800})
    assert propose.cut_points(b, speech_from(b)) == [0, 20, 30]


def test_gapless_speech_offers_only_its_own_edges():
    b = beat_of(40)
    assert propose.cut_points(b, speech_from(b)) == [0, 40]


def test_without_a_vad_nothing_interior_is_offered():
    """No silence map means no evidence, and no evidence means no cutting."""
    b = beat_of(40, gaps={19: 2000})
    assert propose.cut_points(b, None) == [0, 40]


def test_every_enumerated_boundary_is_a_legal_cut_point():
    b = beat_of(60, gaps={9: 700, 19: 900, 29: 600, 39: 1100, 49: 800})
    sm = speech_from(b)
    points = propose.cut_points(b, sm)
    # A cut point sits in the silence *between* two words, so a span starting
    # there begins at words[i], and one ending there ends at words[i-1].
    starts = {b.words[i].start_ms for i in points if i < len(b.words)}
    ends = {b.words[i - 1].end_ms for i in points if i > 0}
    for s in propose.enumerate_spans(b, sm):
        assert s.start_ms in starts, s.text
        assert s.end_ms in ends, s.text


# --- what a span is ----------------------------------------------------------


def test_a_beat_below_the_carve_threshold_is_left_whole():
    """Short enough to already be an editorial unit.

    Expressed against `CARVE_ABOVE_MS` rather than a fixed word count, because
    that threshold is evidence-led and has moved twice: 12s, then 8s, now 4s.
    The last move came from a finished 18-minute assembly whose median clip is
    5.2s and 66% of whose clips are under 8s — a rule that never carved
    anything under 8s was declining to consider the length most editing
    actually uses.

    A test pinned to a word count silently stops testing what it names when the
    threshold moves under it: `beat_of(12)` was 6s of speech, comfortably below
    12s and then 8s, and became carvable at 4s without the assertion changing
    its mind about what it meant.
    """
    words = max(1, int(propose.CARVE_ABOVE_MS / 500) - 2)
    b = beat_of(words, gaps={1: 900})
    assert b.duration_ms < propose.CARVE_ABOVE_MS
    assert propose.enumerate_spans(b, speech_from(b)) == [b]


def test_slivers_are_not_proposed():
    b = beat_of(60, gaps={1: 900, 29: 900})
    for s in propose.enumerate_spans(b, speech_from(b)):
        assert s.duration_ms >= propose.MIN_SPAN_MS


def test_a_span_carries_its_parent_and_what_was_done():
    b = beat_of(60, gaps={29: 900})
    spans = propose.enumerate_spans(b, speech_from(b))
    carved = [s for s in spans if s is not b]
    assert carved, "a 60-word beat with a real pause should carve"
    for s in carved:
        assert s.parent_id == b.id
        assert s.kind in ("trim", "split")
        assert s.id != b.id
        # Text must match the words it actually covers, or the transcript page
        # shows one thing and the AAF contains another.
        assert s.text == " ".join(w.text for w in s.words)


def test_the_original_beat_always_survives_as_a_candidate():
    b = beat_of(60, gaps={29: 900})
    assert b in propose.enumerate_spans(b, speech_from(b))


# --- the model proposer's gate -----------------------------------------------


class FakeProposer:
    """Stands in for the API call, returning whatever indices the test wants."""

    def __init__(self, rows):
        self.rows = rows

    def propose(self, beat, speech, brief):
        legal = set(propose.cut_points(beat, speech))
        out = [beat]
        for lo, hi in self.rows:
            if lo not in legal or hi not in legal or hi <= lo:
                continue
            s = propose.span(beat, lo, hi, "trim", "because")
            if s is not None and s is not beat:
                out.append(s)
        return out


class Brief:
    target_duration_s = 120
    duration_tolerance_s = 10
    narrative_shape = "chronological"
    speaker_priority: list = []
    tone: list = []
    handle_frames = 6
    keep_filler = False


def test_a_proposal_off_a_cut_point_is_dropped_not_snapped():
    """Snapping would move the cut off the thought the rationale describes.

    A model that ignores CUT_POINTS must lose the span, not have it quietly
    relocated to somewhere it never reasoned about.
    """
    b = beat_of(60, gaps={29: 900})
    b.asset_id = "a"
    sm = speech_from(b)
    # 15 is mid-speech and illegal; 30 is the real pause.
    out = propose.build([b], lambda _: sm, Brief(), FakeProposer([(0, 15),
                                                                  (30, 60)]))
    assert len(out) == 2
    assert out[1].start_ms == b.words[30].start_ms


def test_a_failing_proposer_degrades_to_the_original_beat():
    """A degraded cut, not a failed job."""
    class Broken:
        def propose(self, *a):
            raise RuntimeError("API down")

    b = beat_of(60, gaps={29: 900})
    assert propose.build([b], lambda _: None, Brief(), Broken()) == [b]


# --- the solver --------------------------------------------------------------


def spans_for_solver():
    """One parent, offered whole and as two halves."""
    b = beat_of(60, gaps={29: 900})
    b.asset_id = "a"
    sm = speech_from(b)
    whole = b
    first = propose.span(b, 0, 30, "split")
    second = propose.span(b, 30, 60, "split")
    return whole, first, second, sm


def test_the_solver_never_picks_two_spans_that_overlap():
    whole, first, second, _ = spans_for_solver()
    cands = [whole, first, second]
    scores = {c.id: 90.0 for c in cands}

    class B(Brief):
        # Wide enough that taking the whole beat AND a half would fit on
        # duration alone — only the overlap constraint prevents it.
        target_duration_s = 45
        duration_tolerance_s = 25

    picks = select.solve(cands, scores, B(), {"a": 0})
    chosen = [p.beat for p in picks]
    for i, a in enumerate(chosen):
        for c in chosen[i + 1:]:
            assert not a.overlaps(c), f"{a.id} overlaps {c.id}"


def test_disjoint_spans_from_one_parent_are_allowed():
    """Keep the opening and the payoff, drop the middle — a real edit."""
    _, first, second, _ = spans_for_solver()
    scores = {first.id: 90.0, second.id: 90.0}

    class B(Brief):
        target_duration_s = 30
        duration_tolerance_s = 5

    picks = select.solve([first, second], scores, B(), {"a": 0})
    assert len(picks) == 2, "two non-overlapping halves should both be usable"


def test_spans_in_different_assets_never_clash():
    """Frame numbers are per file; identical timings are a coincidence."""
    whole, first, _, _ = spans_for_solver()
    from dataclasses import replace
    other = replace(first, id="other", asset_id="b")
    assert not first.overlaps(other)
