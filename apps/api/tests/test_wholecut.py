"""The whole-transcript selector: one call, and the same hard gate.

Everywhere else, no model sees the whole piece — span proposal is one call per
beat, scoring is windowed, the solver assembles. That decomposition is why the
system struggles with a judgement an article summariser makes easily: what this
cut is about, and therefore what can go.

This path hands one model the entire transcript with each beat's legal cut
points and takes back the finished cut. What it must NOT do is become a
summariser: every span is still a contiguous run of words that were actually
said, still bounded by silence the recording contains.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from mishne.asr.base import Word  # noqa: E402
from mishne.llm.base import Completion  # noqa: E402
from mishne.pipeline.steps import wholecut  # noqa: E402
from mishne.pipeline.steps.structure import Beat  # noqa: E402
from mishne.pipeline.steps.vad import SpeechMap  # noqa: E402

GAP_MS = 500


def _beats(n: int = 3, words_each: int = 40) -> list[Beat]:
    out = []
    for b in range(n):
        words = [Word(text=f"b{b}w{i}", start_ms=i * 1000,
                      end_ms=(i + 1) * 1000 - GAP_MS) for i in range(words_each)]
        out.append(Beat(id=f"beat_{b:04d}", idx=b, speaker="T1", start_ms=0,
                        end_ms=words_each * 1000,
                        text=" ".join(w.text for w in words),
                        words=words, asset_id="ast_1"))
    return out


SPEECH = SpeechMap(
    speech=[(i * 1000, (i + 1) * 1000 - GAP_MS) for i in range(40)],
    duration_ms=40_000,
)


class _Brief:
    target_duration_s = 120

    def to_dict(self):
        return {"target_duration_s": 120, "pacing": "tight", "notes_raw": "x"}


class FakeRouter:
    def __init__(self, rows, truncate_below: int = 0):
        self.rows = rows
        self.user = ""
        self.violations = None
        self.truncate_below = truncate_below
        self.budgets: list[int] = []

    def complete(self, task, *, system, user, max_tokens=8192, **kw):
        self.user = user
        self.system = system
        self.budgets.append(max_tokens)
        if max_tokens < self.truncate_below:
            return Completion(text="[{", model="m", provider="p",
                              output_tokens=max_tokens,
                              stop_reason="max_tokens")
        return Completion(text=json.dumps(self.rows), model="m", provider="p")

    def note_violations(self, task, refused, offered, completion=None):
        self.violations = (refused, offered)

    def mark_unparsed(self, completion):
        pass


def test_one_call_sees_every_beat():
    """The whole point. If this ever stops being true the path is pointless."""
    beats = _beats(3)
    router = FakeRouter([{"beat": "beat_0000", "start": 0, "end": 8, "score": 90}])

    wholecut.propose_cut(beats, lambda _a: SPEECH, _Brief(), router)

    for b in beats:
        assert b.id in router.user
    assert "CUT_POINTS" in router.system or "cut_points" in router.user


def test_a_span_off_the_legal_points_is_refused_not_snapped():
    """Same gate as the per-beat proposer. A model that reasons about prose
    rather than about the recording proposes cuts the audio cannot make, and
    snapping them somewhere nearby would move the cut off the thought."""
    beats = _beats(1)
    router = FakeRouter([
        {"beat": "beat_0000", "start": 0, "end": 8, "score": 90},
        # 7 is not a cut point boundary the beat permits as an END here only if
        # silence covers it; 999 certainly is not.
        {"beat": "beat_0000", "start": 0, "end": 999, "score": 95},
        {"beat": "nonexistent", "start": 0, "end": 4, "score": 95},
    ])

    candidates, scores = wholecut.propose_cut(
        beats, lambda _a: SPEECH, _Brief(), router)

    refused, offered = router.violations
    assert offered == 3
    assert refused == 2
    chosen = [c for c in candidates if scores.get(c.id, 0) > 0]
    assert len(chosen) == 1


def test_unchosen_beats_survive_at_zero():
    """The solver needs something to reach for when the chosen spans cannot
    make the duration window. A beat nobody picked is still real material, and
    omitting it would change what `solve` is choosing between."""
    beats = _beats(3)
    router = FakeRouter([{"beat": "beat_0000", "start": 0, "end": 8, "score": 90}])

    candidates, scores = wholecut.propose_cut(
        beats, lambda _a: SPEECH, _Brief(), router)

    ids = {c.id for c in candidates}
    for b in beats:
        assert b.id in ids
        assert scores[b.id] == 0.0 or b.id == "beat_0000"


def test_the_brief_reaches_the_model_without_the_raw_notes():
    """`notes_raw` is the customer's own words and is not needed once the brief
    is compiled — the same exclusion the windowed scorer makes."""
    router = FakeRouter([])
    wholecut.propose_cut(_beats(1), lambda _a: SPEECH, _Brief(), router)
    assert "target_duration_s" in router.user
    assert "notes_raw" not in router.user


def test_truncation_grows_the_budget_rather_than_splitting_the_question():
    """The windowed scorer halves a truncated window and asks again. This
    question cannot be halved: the entire value of the call is that one model
    saw the whole transcript, and asking about half of it is asking the
    decomposed pipeline's question by another route. So the budget doubles."""
    router = FakeRouter(
        [{"beat": "beat_0000", "start": 0, "end": 8, "score": 90}],
        truncate_below=wholecut.START_MAX_TOKENS * 2,
    )

    candidates, scores = wholecut.propose_cut(
        _beats(1), lambda _a: SPEECH, _Brief(), router)

    assert router.budgets[0] == wholecut.START_MAX_TOKENS
    assert router.budgets[1] == wholecut.START_MAX_TOKENS * 2
    assert any(v > 0 for v in scores.values())


def test_a_ceiling_that_still_truncates_is_reported_not_half_a_cut():
    """Half a cut that looks like a whole one is the worse outcome."""
    from mishne.llm.base import Truncated

    router = FakeRouter([], truncate_below=10 ** 9)
    with pytest.raises(Truncated):
        wholecut.propose_cut(_beats(1), lambda _a: SPEECH, _Brief(), router)
    assert max(router.budgets) == wholecut.CEILING_MAX_TOKENS


def test_the_call_asks_for_more_material_than_the_cut_needs():
    """The solver can only choose between things it was given.

    The first real whole-cut runs returned exactly the target duration, so
    after the clip-length cap filtered the over-long spans there was not enough
    left to fill the window and the cap relaxed — silently reverting to "take
    everything offered". Six spans in, five out: the solver made no decisions,
    and a 57-second clip survived a brief that caps clips at 24.
    """
    router = FakeRouter([])
    wholecut.propose_cut(_beats(1), lambda _a: SPEECH, _Brief(), router)
    assert "twice the target" in router.system


def test_the_timeout_scales_with_the_output_asked_for():
    """A budget escalation once ran past a timeout sized for a small answer.
    `_post` treats a timeout as retryable, so the router failed over and the
    run was labelled with a model that did not produce it."""
    from mishne.llm.providers import _timeout_for

    assert _timeout_for(4096) < _timeout_for(32_768)
    # Enough headroom that a 32k-token generation is not racing the clock.
    assert _timeout_for(32_768) >= 480
