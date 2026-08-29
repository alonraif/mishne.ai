"""One cut, several uploads.

The failure this guards against is not a crash — it is a timeline that opens
cleanly in the NLE and shows the wrong frames, because a beat from one upload
was refined against another's silence map or assembled at another's rate. Every
test here is about coordinates staying attached to the file they describe.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from mishne.asr import Word  # noqa: E402
from mishne.pipeline.steps import assemble, emit, refine, select, validate  # noqa: E402
from mishne.pipeline.steps.structure import Beat  # noqa: E402
from mishne.pipeline.steps.vad import SpeechMap  # noqa: E402
from mishne.timecode import Rate, tc_to_frames  # noqa: E402

PAL = Rate(25, 1)
NTSC = Rate(24000, 1001)


def beat(asset: str, idx: int, start_ms: int, end_ms: int,
         speaker: str = "SPK1") -> Beat:
    words = [Word(text="word", start_ms=start_ms, end_ms=end_ms,
                  speaker=speaker)]
    return Beat(id=f"{asset}_beat_{idx:04d}", idx=idx, speaker=speaker,
                start_ms=start_ms, end_ms=end_ms, text=f"{asset} line {idx}",
                words=words, asset_id=asset)


def speech_every(n: int, span_ms: int, gap_ms: int, start: int = 0) -> SpeechMap:
    """Alternating speech and silence, so snapping always has somewhere to go."""
    seg, t = [], start
    for _ in range(n):
        seg.append((t, t + span_ms))
        t += span_ms + gap_ms
    return SpeechMap(speech=seg, duration_ms=t)


@pytest.fixture
def two_assets():
    """Two uploads: an hour-marked PAL rushes and a 23.976 second camera."""
    a = refine.AssetContext(
        rate=PAL, start_tc_frames=tc_to_frames(10, 0, 0, 0, PAL),
        duration_frames=25 * 600, asset_id="rushes_a",
        speech=speech_every(40, 4000, 1000), order=0)
    b = refine.AssetContext(
        rate=NTSC, start_tc_frames=tc_to_frames(14, 30, 0, 0, NTSC),
        duration_frames=int(NTSC.fps * 600), asset_id="rushes_b",
        speech=speech_every(40, 4000, 1000), order=1)
    return {a.asset_id: a, b.asset_id: b}


def selections(*beats: Beat) -> list[select.Selection]:
    return [select.Selection(b, i, 10.0) for i, b in enumerate(beats)]


# --- stage 9 -----------------------------------------------------------------


def test_each_cut_lands_inside_its_own_asset(two_assets):
    """The clamp is per asset, so a cut can never point outside its own media."""
    picks = selections(
        beat("rushes_a", 0, 5000, 9000),
        beat("rushes_b", 0, 5000, 9000),
        beat("rushes_a", 1, 60000, 64000),
    )
    cuts = refine.refine_multi(picks, two_assets)

    assert [c.asset_id for c in cuts] == ["rushes_a", "rushes_b", "rushes_a"]
    for c in cuts:
        ctx = two_assets[c.asset_id]
        lo = ctx.start_tc_frames
        assert lo <= c.src_in < c.src_out <= lo + ctx.duration_frames


def test_same_local_time_gives_different_frames_per_asset(two_assets):
    """5 s into a 10:00:00:00 PAL reel is not 5 s into a 14:30:00:00 NTSC one.

    Both beats start at the same millisecond. If the two ever produce the same
    frame number, the asset's own start timecode has been lost.
    """
    cuts = refine.refine_multi(selections(
        beat("rushes_a", 0, 5000, 9000),
        beat("rushes_b", 0, 5000, 9000)), two_assets)
    a, b = cuts
    assert a.src_in != b.src_in

    def offset_s(cut):
        ctx = two_assets[cut.asset_id]
        return (cut.src_in - ctx.start_tc_frames) / ctx.rate.fps

    # Same instant into each reel, expressed in each reel's own frames.
    assert offset_s(a) == pytest.approx(offset_s(b), abs=0.05)


def test_clips_from_different_assets_never_merge(two_assets):
    """Contiguous frame numbers across two files are a coincidence, not a seam.

    Constructed so the numbers genuinely do line up: without the asset check
    these two would fuse into one clip spanning two different shoots.
    """
    a_ctx = two_assets["rushes_a"]
    twin = refine.AssetContext(
        rate=PAL, start_tc_frames=a_ctx.start_tc_frames,
        duration_frames=a_ctx.duration_frames, asset_id="rushes_c",
        speech=a_ctx.speech, order=2)
    ctxs = {**two_assets, "rushes_c": twin}

    cuts = refine.refine_multi(selections(
        beat("rushes_a", 0, 5000, 9000),
        beat("rushes_c", 0, 9200, 13000)), ctxs)

    assert len(cuts) == 2
    assert {c.asset_id for c in cuts} == {"rushes_a", "rushes_c"}


def test_adjacent_in_the_same_asset_still_merges(two_assets):
    """The single-asset behaviour survives the multi-asset rewrite."""
    cuts = refine.refine_multi(selections(
        beat("rushes_a", 0, 5000, 9000),
        beat("rushes_a", 1, 10000, 14000)), two_assets)
    assert len(cuts) == 1


def test_handles_never_make_a_region_play_twice(two_assets):
    """Consecutive beats with no silence between them are one clip.

    Adding six frames of handle to each side of two touching beats makes their
    source ranges overlap. Emitted as two clips, that overlap plays twice — a
    quarter-second stutter, right where the cut should be seamless.
    """
    cuts = refine.refine_multi(selections(
        beat("rushes_a", 0, 5000, 6000),
        beat("rushes_a", 1, 6000, 7000)), two_assets)
    assert len(cuts) == 1


def test_reuse_of_the_same_region_is_left_alone(two_assets):
    """Overlapping in source but far apart in the record is deliberate."""
    cuts = refine.refine_multi(selections(
        beat("rushes_a", 0, 5000, 6000),
        beat("rushes_b", 0, 5000, 9000),
        beat("rushes_a", 1, 6000, 7000)), two_assets)
    assert len(cuts) == 3


def test_interleaved_neighbours_do_not_merge(two_assets):
    """Two clips from one asset with another asset's clip between them.

    They are adjacent in that asset's subset but not in the finished cut, so
    fusing them would silently reorder the timeline.
    """
    cuts = refine.refine_multi(selections(
        beat("rushes_a", 0, 5000, 9000),
        beat("rushes_b", 0, 5000, 9000),
        beat("rushes_a", 1, 9200, 13000)), two_assets)
    assert len(cuts) == 3
    assert [c.order_idx for c in cuts] == [0, 1, 2]


def test_unknown_asset_is_a_hard_failure(two_assets):
    """Better to stop than to refine against whatever context comes first."""
    with pytest.raises(KeyError):
        refine.refine_multi(selections(beat("never_ingested", 0, 1000, 5000)),
                            two_assets)


def test_single_asset_entry_point_unchanged(two_assets):
    """`refine()` is now a map of one; it must behave exactly as before."""
    ctx = two_assets["rushes_a"]
    picks = selections(beat("rushes_a", 0, 5000, 9000),
                       beat("rushes_a", 1, 60000, 64000))
    old = refine.refine(picks, ctx.speech, ctx.rate, ctx.start_tc_frames,
                        ctx.duration_frames)
    new = refine.refine_multi(picks, {"rushes_a": ctx})
    assert [(c.src_in, c.src_out) for c in old] == \
           [(c.src_in, c.src_out) for c in new]


# --- ordering ----------------------------------------------------------------


def test_chronological_orders_by_asset_then_time():
    """Without asset position, three interviews interleave into nonsense."""
    beats = [beat("b", 0, 1000, 5000), beat("a", 0, 90000, 94000),
             beat("a", 1, 2000, 6000)]
    key = select._source_key({"a": 0, "b": 1})
    assert [b.id for b in sorted(beats, key=key)] == [
        "a_beat_0001", "a_beat_0000", "b_beat_0000"]


# --- stage 10 ----------------------------------------------------------------


def assets_for(ctxs, tmp_path):
    return {aid: assemble.AssetRef(
        rate=c.rate, start_tc_frames=c.start_tc_frames,
        duration_frames=c.duration_frames, asset_id=aid,
        media_path=tmp_path / f"{aid}.mov") for aid, c in ctxs.items()}


def test_timeline_references_the_right_file_per_clip(two_assets, tmp_path):
    cuts = refine.refine_multi(selections(
        beat("rushes_a", 0, 5000, 9000),
        beat("rushes_b", 0, 5000, 9000),
        beat("rushes_a", 1, 60000, 64000)), two_assets)
    timeline = assemble.build_multi(cuts, assets_for(two_assets, tmp_path))

    clips = list(timeline.tracks[0].find_clips())
    assert len(clips) == 3
    for clip, cut in zip(clips, cuts):
        assert clip.metadata["mishne"]["asset_id"] == cut.asset_id
        assert cut.asset_id in clip.media_reference.target_url
        # Conformed to the sequence rate — the AAF writer accepts nothing else
        # — but at the same instant the cut named in its own asset's frames.
        ctx = two_assets[cut.asset_id]
        assert clip.source_range.start_time.rate == pytest.approx(PAL.fps)
        assert (clip.source_range.start_time.value / PAL.fps
                == pytest.approx(cut.src_in / ctx.rate.fps, abs=0.05))
        # And it still sits inside the media it points at.
        avail = clip.media_reference.available_range
        assert avail.start_time.value <= clip.source_range.start_time.value
        assert (clip.source_range.end_time_exclusive().value
                <= avail.end_time_exclusive().value)


def test_mixed_rates_are_reported_not_silently_conformed(two_assets, tmp_path):
    warnings = assemble.warnings_for(assets_for(two_assets, tmp_path))
    assert warnings and "mixed frame rates" in warnings[0]


def test_uniform_rates_warn_about_nothing(two_assets, tmp_path):
    same = {k: v for k, v in two_assets.items() if k == "rushes_a"}
    assert assemble.warnings_for(assets_for(same, tmp_path)) == []


def test_every_asset_gets_a_distinct_mob_id(two_assets, tmp_path):
    """Two files must never share a relink key, whatever their frame numbers."""
    cuts = refine.refine_multi(selections(
        beat("rushes_a", 0, 5000, 9000),
        beat("rushes_b", 0, 5000, 9000)), two_assets)
    timeline = assemble.build_multi(cuts, assets_for(two_assets, tmp_path))
    ids = {c.media_reference.metadata.get("AAF", {}).get("MobID")
           for c in timeline.tracks[0].find_clips()}
    assert len(ids) == 2 and None not in ids


def test_cut_from_an_unsupplied_asset_is_a_hard_failure(two_assets, tmp_path):
    cuts = refine.refine_multi(selections(beat("rushes_a", 0, 5000, 9000)),
                               two_assets)
    others = {k: v for k, v in assets_for(two_assets, tmp_path).items()
              if k != "rushes_a"}
    with pytest.raises(KeyError):
        assemble.build_multi(cuts, others)


# --- stages 11-12: the whole point --------------------------------------------


def test_multi_asset_timeline_round_trips_through_every_format(
        two_assets, tmp_path):
    """A two-source rough cut must survive AAF, FCPXML, EDL and OTIO.

    This is the gate. Everything above can be right and this still fail, which
    is exactly what happened the first time an AAF was written with per-clip
    media extents.
    """
    cuts = refine.refine_multi(selections(
        beat("rushes_a", 0, 5000, 9000),
        beat("rushes_b", 0, 5000, 9000),
        beat("rushes_a", 1, 60000, 64000),
        beat("rushes_b", 1, 120000, 126000)), two_assets)
    timeline = assemble.build_multi(cuts, assets_for(two_assets, tmp_path))

    artifacts = emit.emit(timeline, tmp_path / "out", "multi")
    failed = [a for a in artifacts if not a.ok]
    assert not failed, [(a.fmt, a.error) for a in failed]

    checks = validate.validate(timeline, artifacts, PAL)
    bad = [c for c in checks if not c.ok]
    assert not bad, [(c.fmt, c.error,
                      [(k.name, k.detail) for k in c.checks if not k.ok])
                     for c in bad]
