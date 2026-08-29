"""End to end on real files: two uploads, one cut, four artifacts.

Everything above this test can pass on synthetic dataclasses and still fail on
contact with a real file, because probing, extracting, caching and assembling
each have their own idea of what a frame is. So this builds actual media with
ffmpeg — two reels, different rates, different start timecodes — and runs the
whole job through `project.ingest`.

Two steps are stubbed, and for the same reason: they are the two that need a
human voice. Whisper needs a downloaded model and a minute of CPU, and Silero
VAD detects *speech* — a tone generator produces none, however loud. Both are
replaced with the block layout the fixture was built to, so everything
downstream sees a realistic silence map. What this test is for lives in the
coordinates, not the words, and nothing about probing, extracting, caching,
refining or assembling is stubbed.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from mishne.asr import ASRResult, Word  # noqa: E402
from mishne.pipeline import project  # noqa: E402
from mishne.pipeline.steps import (  # noqa: E402
    assemble, emit, refine, select, transcribe, vad, validate,
)
from mishne.pipeline.steps.vad import SpeechMap  # noqa: E402
from mishne.timecode import Rate, tc_to_frames  # noqa: E402

pytestmark = pytest.mark.skipif(
    shutil.which("ffmpeg") is None, reason="ffmpeg not installed")

PAL = Rate(25, 1)
NTSC = Rate(24000, 1001)

# Four seconds of tone, then silence, repeated. The gap has to clear
# structure.BEAT_PAUSE_MS (1200 ms) or the reel comes back as one 40-second
# beat — which is exactly what happened the first time this test ran.
SPAN_MS, GAP_MS, BLOCKS = 4000, 1500, 8
DURATION_S = BLOCKS * (SPAN_MS + GAP_MS) // 1000


def make_media(path: Path, fps: str, timecode: str, hz: int) -> Path:
    """A real file: video at a real rate, and two microphones.

    Two separate audio streams, not two channels — that is what production
    multi-track material is, and it is what puts the deterministic attribution
    path under test rather than the diarizer. Each mic is loud on alternating
    blocks, so "who is speaking" has an unambiguous answer at every moment.
    """
    period = (SPAN_MS + GAP_MS) / 1000
    # Mic 1 is loud on even blocks, mic 2 on odd ones.
    on = f"lt(mod(t,{period}),{SPAN_MS / 1000})"
    even = f"lt(mod(t,{period * 2}),{period})"
    subprocess.run([
        "ffmpeg", "-y", "-loglevel", "error",
        "-f", "lavfi", "-i", f"color=c=black:s=320x180:r={fps}:d={DURATION_S}",
        "-f", "lavfi", "-i",
        f"sine=frequency={hz}:sample_rate=48000:duration={DURATION_S}",
        "-f", "lavfi", "-i",
        f"sine=frequency={hz * 2}:sample_rate=48000:duration={DURATION_S}",
        "-filter_complex",
        f"[1:a]volume='({on})*(({even})*0.9+0.05)':eval=frame[a1];"
        f"[2:a]volume='({on})*((1-({even}))*0.9+0.05)':eval=frame[a2]",
        "-map", "0:v", "-map", "[a1]", "-map", "[a2]",
        "-timecode", timecode,
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "pcm_s16le",
        "-shortest", str(path)], check=True)
    return path


def fake_asr(words_per_block: int = 6):
    """Words laid inside the tone blocks, so beats line up with real silence."""
    words = []
    for blk in range(BLOCKS):
        base = blk * (SPAN_MS + GAP_MS)
        step = SPAN_MS // words_per_block
        for i in range(words_per_block):
            words.append(Word(
                text=f"word{blk}{i}" + ("." if i == words_per_block - 1 else ""),
                start_ms=base + i * step, end_ms=base + (i + 1) * step - 40,
                confidence=0.95, speaker=""))
    return ASRResult(words=words, language="en", provider="stub", model="stub")


def fake_speech() -> SpeechMap:
    """The silence map the fixture's audio would have, if a tone were a voice."""
    seg, t = [], 0
    for _ in range(BLOCKS):
        seg.append((t, t + SPAN_MS))
        t += SPAN_MS + GAP_MS
    return SpeechMap(speech=seg, duration_ms=t)


@pytest.fixture
def stub_asr(monkeypatch):
    monkeypatch.setattr(transcribe, "run",
                        lambda *a, **k: fake_asr(), raising=True)
    monkeypatch.setattr(vad, "build", lambda *a, **k: fake_speech(),
                        raising=True)


@pytest.fixture
def two_reels(tmp_path):
    return [
        make_media(tmp_path / "day1_studio.mov", "25", "10:00:00:00", 300),
        make_media(tmp_path / "day2_location.mov", "24000/1001",
                   "14:30:00:00", 440),
    ]


class Brief:
    """Enough of a brief to drive selection without invoking the LLM path."""
    target_duration_s = 12
    duration_tolerance_s = 8
    narrative_shape = "chronological"
    speaker_priority: list = []
    handle_frames = 6
    keep_filler = False


def test_ingest_reads_each_reel_on_its_own_terms(two_reels, tmp_path, stub_asr):
    work = tmp_path / "work"
    a = project.ingest(two_reels[0], work, assume_rate=PAL)
    b = project.ingest(two_reels[1], work, assume_rate=NTSC)

    assert (a.rate.num, a.rate.den) == (25, 1)
    assert (b.rate.num, b.rate.den) == (24000, 1001)
    assert a.start_tc_frames == tc_to_frames(10, 0, 0, 0, PAL)
    assert b.start_tc_frames == tc_to_frames(14, 30, 0, 0, NTSC)
    assert a.asset_id != b.asset_id
    assert a.beats and b.beats
    # Every beat knows which reel it came from, and ids cannot collide.
    assert all(x.asset_id == a.asset_id for x in a.beats)
    assert not {x.id for x in a.beats} & {x.id for x in b.beats}
    assert a.speech is not None and len(a.speech.speech) > 1
    # The reel splits into beats rather than arriving as one long block.
    assert len(a.beats) == BLOCKS
    # Two microphones, so attribution is arithmetic and needs no model.
    assert a.audio_tracks == 2
    assert len(a.speakers) == 2 and a.attribution.reliable
    assert all(s.source == "track" for s in a.speakers)


def test_transcription_is_never_paid_for_twice(two_reels, tmp_path,
                                               monkeypatch):
    """The cache is the reason separated uploads are affordable at all."""
    work = tmp_path / "work"
    calls = []

    def counted(*a, **k):
        calls.append(1)
        return fake_asr()

    monkeypatch.setattr(transcribe, "run", counted, raising=True)
    monkeypatch.setattr(vad, "build", lambda *a, **k: fake_speech(),
                        raising=True)

    first = project.ingest(two_reels[0], work, assume_rate=PAL)
    again = project.ingest(two_reels[0], work, assume_rate=PAL)

    assert len(calls) == 1
    assert again.asset_id == first.asset_id
    assert [x.id for x in again.beats] == [x.id for x in first.beats]
    assert again.start_tc_frames == first.start_tc_frames
    assert (again.rate.num, again.rate.den) == (first.rate.num, first.rate.den)


def test_one_cut_from_two_reels_validates_in_every_format(
        two_reels, tmp_path, stub_asr):
    """The whole job, on real files, through to artifacts on disk."""
    work = tmp_path / "work"
    assets = [project.ingest(two_reels[0], work, assume_rate=PAL),
              project.ingest(two_reels[1], work, assume_rate=NTSC)]

    speakers = project.unify_speakers(assets)
    beats = [b for a in assets for b in a.beats]
    scores = {b.id: 10.0 for b in beats}

    picks = select.solve(beats, scores, Brief(), project.asset_order(assets))
    assert picks, "solver found nothing in a fully-scored corpus"

    cuts = refine.refine_multi(picks, project.contexts(assets))
    assert cuts
    assert {c.asset_id for c in cuts} <= {a.asset_id for a in assets}

    refs = project.asset_refs(assets)
    timeline = assemble.build_multi(cuts, refs, name="two_reels")

    artifacts = emit.emit(timeline, tmp_path / "out", "two_reels")
    failed = [a for a in artifacts if not a.ok]
    assert not failed, [(a.fmt, a.error) for a in failed]

    checks = validate.validate(timeline, artifacts, assets[0].rate)
    bad = [c for c in checks if not c.ok]
    assert not bad, [(c.fmt, c.error,
                      [(k.name, k.detail) for k in c.checks if not k.ok])
                     for c in bad]

    # And the speaker list is namespaced, so no two reels claim one person.
    assert len(speakers) == 4
    assert all(":" in s.id for s in speakers)


def test_merging_two_reels_speakers_makes_them_one(two_reels, tmp_path,
                                                   stub_asr):
    work = tmp_path / "work"
    assets = [project.ingest(two_reels[0], work, assume_rate=PAL),
              project.ingest(two_reels[1], work, assume_rate=NTSC)]

    apart = project.unify_speakers(assets)
    assert len(apart) == 4
    # The loudest voice on each reel — the same person on two shoot days, as
    # far as the product is concerned, and two rows until somebody says so.
    canonical = next(s.id for s in apart if s.id.startswith(assets[0].asset_id))
    other = next(s.id for s in apart if s.id.startswith(assets[1].asset_id))

    # Re-ingest so beats are not already rewritten by the call above.
    assets = [project.ingest(two_reels[0], work, assume_rate=PAL),
              project.ingest(two_reels[1], work, assume_rate=NTSC)]
    joined = project.unify_speakers(assets, {other: canonical})

    assert len(joined) == 3
    merged = next(s for s in joined if s.id == canonical)
    assert " · " not in merged.default_label
    assert other not in {s.id for s in joined}
    assert canonical in {b.speaker for a in assets for b in a.beats}


# --- single-track material ---------------------------------------------------


def make_mono(path: Path, tmp_path: Path) -> Path:
    """One microphone. The case that used to invent a speaker."""
    subprocess.run([
        "ffmpeg", "-y", "-loglevel", "error", "-i", str(path),
        "-map", "0:v", "-map", "0:a:0", "-c", "copy", str(tmp_path)],
        check=True)
    return tmp_path


def test_single_track_reports_no_speakers_rather_than_inventing_one(
        two_reels, tmp_path, stub_asr):
    """One track cannot say who is talking, and must not pretend it can.

    The old behaviour returned one speaker with `reliable=True`. On a three-way
    conversation that is not a simplification — it is a false statement the UI
    renders as fact beside every line.
    """
    mono = make_mono(two_reels[0], tmp_path / "mono.mov")
    a = project.ingest(mono, tmp_path / "work", assume_rate=PAL)

    assert a.audio_tracks == 1
    assert a.speakers == []
    assert not a.attribution.reliable
    assert any("never separated" in n for n in a.attribution.notes)


def test_diarization_is_used_when_a_model_is_supplied(two_reels, tmp_path,
                                                      stub_asr, monkeypatch):
    """The wiring: one track plus a diarizer means turns become speakers.

    The provider is stubbed. What is under test is that ingest reaches for it at
    all, hands it the material's own source regions, and turns what comes back
    into attributed words — not the acoustics, which need real voices.
    """
    from mishne.diarize.base import DiarizationResult, Turn

    seen = {}

    class StubProvider:
        name = "stub"

        def diarize(self, audio_path, regions=None):
            seen["regions"] = regions
            half = BLOCKS // 2 * (SPAN_MS + GAP_MS)
            return DiarizationResult(
                turns=[Turn(0, half, "S1"),
                       Turn(half, BLOCKS * (SPAN_MS + GAP_MS), "S2")],
                provider="stub", model="stub")

    monkeypatch.setattr("mishne.diarize.get_provider",
                        lambda *a, **k: StubProvider())

    mono = make_mono(two_reels[0], tmp_path / "mono.mov")
    a = project.ingest(mono, tmp_path / "work", assume_rate=PAL,
                       diarize_models=tmp_path)

    assert [s.id for s in a.speakers] == ["S1", "S2"]
    assert all(s.source == "diarization" for s in a.speakers)
    assert all(not s.confirmed for s in a.speakers), "naming stays a human act"
    # Plain media carries no human seams, so the diarizer gets no regions.
    assert seen["regions"] is None
    assert {b.speaker for b in a.beats} == {"S1", "S2"}
