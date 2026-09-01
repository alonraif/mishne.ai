"""Multi-track attribution is pure given envelopes, so it pins down properly."""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from mishne.asr import Word  # noqa: E402
from mishne.pipeline.steps import speakers  # noqa: E402

HOP = speakers.HOP_MS


def env(segments: list[tuple[int, int, float]], total_ms: int = 10_000,
        floor: float = 0.001) -> np.ndarray:
    """Envelope with `floor` everywhere and given level in [start_ms, end_ms)."""
    e = np.full(total_ms // HOP, floor, dtype=np.float32)
    for start, end, level in segments:
        e[start // HOP:end // HOP] = level
    return e


def words_at(spans: list[tuple[int, int]]) -> list[Word]:
    return [Word(f"w{i}", s, e, 0.9) for i, (s, e) in enumerate(spans)]


def test_loudest_track_wins():
    ws = words_at([(1000, 1500), (5000, 5500)])
    envelopes = {
        1: env([(1000, 1500, 0.5)]),
        2: env([(5000, 5500, 0.5)]),
    }
    speakers.attribute(ws, envelopes)
    assert ws[0].speaker == "T1"
    assert ws[1].speaker == "T2"


def test_gain_difference_does_not_decide_the_speaker():
    """The whole point of per-track normalisation.

    Track 2 is recorded 10x hotter than track 1. Without normalisation it wins
    every word. With it, each track is judged against its own speech level and
    the person actually talking wins.
    """
    ws = words_at([(1000, 1500)])
    envelopes = {
        1: env([(1000, 1500, 0.5), (6000, 6500, 0.5)], floor=0.001),
        2: env([(6000, 6500, 5.0)], floor=0.01),   # hot mic, silent at 1000ms
    }
    speakers.attribute(ws, envelopes)
    assert ws[0].speaker == "T1", "hot mic must not win a word it did not hear"


def test_crosstalk_flagged_when_levels_are_close():
    ws = words_at([(1000, 1500)])
    envelopes = {
        1: env([(1000, 1500, 0.50)]),
        2: env([(1000, 1500, 0.45)]),   # within the margin
    }
    result = speakers.attribute(ws, envelopes)
    assert result.crosstalk_words == 1
    assert ws[0].speaker in ("T1", "T2"), "still attributed, but flagged"


def test_clear_lead_is_not_crosstalk():
    """Realistic bleed: mic 2 carries its owner's voice loudly *elsewhere*, so
    the bleed it picks up during mic 1's word normalises to well below 1.0."""
    ws = words_at([(1000, 1500)])
    envelopes = {
        1: env([(1000, 1500, 0.5), (6000, 6500, 0.02)]),   # speech, then bleed
        2: env([(1000, 1500, 0.05), (6000, 6500, 0.5)]),   # bleed, then speech
    }
    result = speakers.attribute(ws, envelopes)
    assert ws[0].speaker == "T1"
    assert result.crosstalk_words == 0


def test_bleed_only_track_ties_and_is_flagged_not_guessed():
    """Known limitation, deliberately documented.

    A track that never carries its owner's voice — someone mic'd who never
    speaks, or a channel picking up only bleed — has no real speech to set a
    reference from, so its bleed *becomes* its reference and normalises to 1.0.
    It then ties with the person actually talking.

    The algorithm does not resolve this, and should not: with no absolute
    reference there is no principled way to tell "quiet speaker" from
    "bleed-only channel". What it does instead is refuse to be confident — the
    word is attributed to the leader but flagged as crosstalk, so the failure
    surfaces in the UI rather than becoming a silently wrong speaker label.

    Worth revisiting with real multi-track material. Adding a guard now would be
    tuning against a case nobody has measured.
    """
    ws = words_at([(1000, 1500)])
    envelopes = {
        1: env([(1000, 1500, 0.5)]),
        2: env([(1000, 1500, 0.05)]),   # bleed and nothing else, ever
    }
    result = speakers.attribute(ws, envelopes)
    assert result.crosstalk_words == 1, "ties must be flagged, never guessed"


def test_heavy_crosstalk_marks_attribution_unreliable():
    ws = words_at([(i * 500, i * 500 + 400) for i in range(2, 18)])
    envelopes = {
        1: env([(0, 10_000, 0.50)]),
        2: env([(0, 10_000, 0.48)]),
    }
    result = speakers.attribute(ws, envelopes)
    assert result.reliable is False
    assert any("unreliable" in n for n in result.notes)


def test_unused_track_is_dropped():
    ws = words_at([(1000, 1500)])
    envelopes = {
        1: env([(1000, 1500, 0.5)]),
        2: env([], floor=0.0001),        # dead channel
    }
    result = speakers.attribute(ws, envelopes)
    assert [s.id for s in result.speakers] == ["T1"]


def test_single_track_says_so_rather_than_inventing_speakers():
    ws = words_at([(1000, 1500), (5000, 5500)])
    result = speakers.attribute(ws, {1: env([(0, 10_000, 0.5)])})
    assert len(result.speakers) == 1
    assert result.speakers[0].default_label == "Speaker 1"
    assert any("diarization" in n for n in result.notes)


def test_silence_leaves_word_unattributed():
    ws = words_at([(8000, 8400)])
    envelopes = {
        1: env([(1000, 1500, 0.5)], floor=0.0001),
        2: env([(5000, 5500, 0.5)], floor=0.0001),
    }
    result = speakers.attribute(ws, envelopes)
    assert ws[0].speaker == ""
    assert result.unattributed_words == 1


def test_speaker_display_prefers_the_human_label():
    s = speakers.Speaker(id="T1", source="track", default_label="Mic 1")
    assert s.display == "Mic 1"
    s.label = "Margret Olsen"
    assert s.display == "Margret Olsen"


def test_no_tracks_is_not_reliable():
    result = speakers.attribute([], {})
    assert result.reliable is False


def test_a_beat_takes_the_speaker_attribution_gave_the_word_not_the_vendors():
    """Why `speakers` has to run before `structure`, at the level where it bites.

    Attribution rewrites `Word.speaker` in place and segmentation snapshots it:
    a beat is labelled with the speaker of its first word, once, when it is
    built. So the two stages do not commute. The orchestrator ran them the other
    way round and every beat it cached kept the label the ASR vendor returned
    (`c0:spk:0` from a chunked Gemini call) while the legend offered `T1`/`T2`
    from the microphones — beats and speakers in two id spaces, which the UI can
    only render as a raw vendor id with no colour and a filter that matches
    nothing.

    The order below is `project.ingest`'s. The assertion is what makes it the
    right one.
    """
    from mishne.pipeline.steps import structure
    from mishne.pipeline.steps.vad import SpeechMap

    ws = words_at([(1000, 1500), (5000, 5500)])
    for w in ws:                      # what a diarizing engine hands back
        w.speaker = "c0:spk:0"
    envelopes = {1: env([(1000, 1500, 0.5)]), 2: env([(5000, 5500, 0.5)])}

    attribution = speakers.attribute(ws, envelopes)
    beats = structure.build(ws, SpeechMap(speech=[(1000, 1500), (5000, 5500)],
                                          duration_ms=10_000),
                            asset_id="a_test", seams=[])

    roster = {s.id for s in attribution.speakers}
    assert roster == {"T1", "T2"}
    assert {b.speaker for b in beats} <= roster, (
        "a beat is labelled with a speaker the legend has never heard of"
    )
