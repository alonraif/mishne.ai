"""A cached re-run must produce the same cut as the run that filled the cache.

ADR-0016 says resume is idempotent re-execution rather than a checkpoint
restore, and the whole multi-upload story (ADR-0008) rests on "add a reel and
re-cut is cheap". Both claims quietly assume the cheap path produces the *same*
answer, and nothing asserted it.

It did not. `_save` wrote every beat without its words, so a restored beat had
an empty `words` list. `cut_points` returns indices into that list, and with no
words it returns the beat's own two edges — so `enumerate_spans` carved nothing
and `ModelProposer` built its prompt from an empty list and made no call at all.

The visible effect was a coarser cut on every run after the first. On a
25.7-minute interview the scorer was offered 61 whole beats of median 27.3s
instead of 98 candidates including 37 carved out of long blocks, so a
two-minute promo could only be assembled from ~27-second slabs. That reads as a
model with no editorial courage; it was a model that was never shown the
alternatives.

The existing cache test asserted that a second run **transcribes nothing** —
true throughout, and orthogonal. Cheap and correct are different properties.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from mishne.asr.base import Word  # noqa: E402
from mishne.pipeline import project  # noqa: E402
from mishne.pipeline.steps.propose import cut_points, enumerate_spans  # noqa: E402
from mishne.pipeline.steps.structure import Beat  # noqa: E402


#: Each word is followed by a gap wide enough to clear MIN_CUT_SILENCE_MS, so
#: every boundary is a legal cut point. A shorter gap is correctly refused by
#: the silence gate (ADR-0010) and would make this test about the gate rather
#: than about the cache.
GAP_MS = 500


def _beat_with_words(n_words: int = 40, ms_each: int = 1_000) -> Beat:
    words = [
        Word(text=f"w{i}", start_ms=i * ms_each,
             end_ms=(i + 1) * ms_each - GAP_MS, confidence=0.9)
        for i in range(n_words)
    ]
    return Beat(id="beat_0001", idx=1, speaker="T1", start_ms=0,
                end_ms=n_words * ms_each, text=" ".join(w.text for w in words),
                words=words, asset_id="ast_1")


def _roundtrip(beat: Beat, tmp_path: Path) -> Beat:
    """Through `_save` and `_load`, which is what a cache hit does."""
    from mishne.pipeline.steps import speakers as spk
    from mishne.timecode import Rate

    ingest = project.AssetIngest(
        asset_id="ast_1", path=tmp_path / "a.mov", rate=Rate(25, 1, False),
        start_tc_frames=0, duration_frames=1000, language="en",
        beats=[beat], speakers=[], attribution=spk.Attribution(speakers=[]),
        # No audio on disk, so `_load` rebuilds no speech map. That is the
        # honest shape of this test: it isolates the words, which are the thing
        # that was lost, from the silence map, which was always rebuilt.
        speech=None, audio_path=None,
    )
    cached = tmp_path / "ingest.json"
    project._save(ingest, cached)
    loaded = project._load(cached, tmp_path / "a.mov", tmp_path)
    assert loaded is not None, "a cache written by this version must load"
    return loaded.beats[0]


def test_a_cached_beat_keeps_its_words(tmp_path):
    """The one line that caused all of it."""
    original = _beat_with_words()
    restored = _roundtrip(original, tmp_path)

    assert len(restored.words) == len(original.words)
    assert [w.text for w in restored.words] == [w.text for w in original.words]
    # Timings, not just text: the cut points are word boundaries in milliseconds
    # and a word restored without them is not a cut point.
    assert [w.start_ms for w in restored.words] == [w.start_ms for w in original.words]
    assert [w.end_ms for w in restored.words] == [w.end_ms for w in original.words]


def test_a_cached_beat_can_still_be_carved(tmp_path):
    """The consequence that reached the customer.

    A beat with no words has exactly two legal cut points — its own edges — so
    it is offered to the scorer whole and the cut is as coarse as the beats.
    """
    original = _beat_with_words()
    restored = _roundtrip(original, tmp_path)

    # Speech with a real gap between every word, so every word boundary is a
    # legal cut point and the only thing that can remove them is losing the
    # words themselves.
    from mishne.pipeline.steps.vad import SpeechMap

    speech = SpeechMap(
        speech=[(i * 1_000, (i + 1) * 1_000 - GAP_MS) for i in range(40)],
        duration_ms=40 * 1_000,
    )

    assert len(cut_points(original, speech)) > 2
    assert len(cut_points(restored, speech)) == len(cut_points(original, speech))
    assert len(enumerate_spans(restored, speech)) == len(
        enumerate_spans(original, speech)
    )


def test_a_cache_written_before_words_is_rebuilt_not_served(tmp_path):
    """An entry from the previous version has no words in it, and serving it
    would reproduce the bug from a file rather than from the code. That is what
    CACHE_VERSION is for."""
    import json

    original = _beat_with_words()
    cached = tmp_path / "ingest.json"
    _roundtrip(original, tmp_path)

    stale = json.loads(cached.read_text())
    stale["cacheVersion"] = 2
    for b in stale["beats"]:
        b.pop("words", None)
    cached.write_text(json.dumps(stale))

    assert project._load(cached, tmp_path / "a.mov", tmp_path) is None
