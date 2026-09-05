"""Stage 10 — assemble the canonical timeline.

Builds one OpenTimelineIO document. **This is the record of truth for what the
edit was.** Every output format is a projection of it, no format is ever
generated from another, and a support engineer debugging a bad export starts
here. See docs/adr/0001-otio-as-canonical-timeline.md.

## Several assets in one timeline

A project accumulates uploads over weeks and one finished piece is cut from
more than one of them, so a cut is not a list of positions in a file — it is a
list of positions each in *its own* file. `build_multi` takes the cuts and the
assets they refer to and resolves each cut against the right one; the
single-asset `build` and `build_from_aaf` are the same machinery with a map of
one, which is how they stay honest as the multi-asset path evolves.

Two things do not average across assets:

* **Frame rate.** A timeline has exactly one. The first asset the job names
  supplies it, and any asset that disagrees is reported — `warnings_for` returns
  what the caller should say out loud.

  OTIO itself carries a rate per time value, so the tempting thing is to leave
  each clip's source range at its own asset's rate and let the document be
  precisely right. **The AAF writer rejects that outright**: every clip
  duration and every media extent must match the sequence rate, and a
  two-source cut fails validation before it reaches disk. So frame numbers are
  conformed to the sequence rate at the moment of assembly, by `_conform`,
  which preserves the instant rather than the number. This is the one place it
  happens and the one place to look when a mixed-rate cut lands a frame off.
* **Media identity.** Clips are grouped by mob ID, and mob IDs are per asset.
  Two files' frame numbering will collide near the start of both, which is
  exactly the sort of thing that produces a timeline that opens fine and shows
  the wrong shot.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import opentimelineio as otio
from opentimelineio.opentime import RationalTime, TimeRange

from ...interchange import mobid
from ...timecode import Rate, tc_to_frames
from .aaf_ingest import AAFSource, basename_of, map_to_source
from .refine import Cut


@dataclass
class AssetRef:
    """One upload, as stage 10 needs to see it.

    Either `media_path` (a file the NLE can open) or `aaf` (a sequence whose own
    clips are the real sources) is set. An AAF asset resolves each cut back
    through its source map; a media asset points straight at the file.

    **`media_path` is where the bytes are right now; `display_name` is what the
    customer calls them, and the artifacts must carry the second.** On a worker
    those are not the same string: the file has been staged into a scratch
    directory under a sanitised name (`workspace._safe_name`, which turns
    `Tia Mowry 'My Next Act,' (360p)` into `Tia Mowry _My Next Act__ _360p_`),
    and that directory is deleted the moment the job completes. An artifact
    naming the staged file names a path that never existed on the customer's
    machine, under a filename their media does not have — which is two
    independent reasons no NLE can relink it. `display_name` defaults to the
    path's own name, which is exactly right for `run.py`, where the file the
    pipeline read *is* the customer's file.
    """

    rate: Rate
    start_tc_frames: int
    duration_frames: int
    asset_id: str = ""
    media_path: Path | None = None
    aaf: AAFSource | None = None
    audio_tracks: int = 1
    #: The customer's filename, with its extension. Empty means `media_path`'s.
    display_name: str = ""
    #: True when `media_path` is a temporary copy this run made and will delete,
    #: rather than the customer's own file. Decides whether the artifacts may
    #: name a location at all — see `media_url`.
    staged: bool = False
    #: Frame size, for the AAF essence descriptor and the FCPXML format name.
    #: Zero means unknown, and the writers keep their own defaults.
    width: int = 0
    height: int = 0
    # Filled lazily on first use — one extent per mob ID, see `_extents`.
    _extents: dict = field(default=None, repr=False)

    @property
    def filename(self) -> str:
        """What the media is called, wherever it now lives."""
        if self.display_name:
            return self.display_name
        if self.media_path is not None:
            return self.media_path.name
        return ""

    @property
    def name(self) -> str:
        if self.media_path is not None or self.display_name:
            return Path(self.filename).stem
        return self.asset_id or "source"

    @property
    def has_picture(self) -> bool:
        """Whether this asset has a picture track to cut.

        Frame geometry is the probe's answer for a flat upload: an audio file
        has none, and a video file has both dimensions (ADR-0005).

        A sequence is asked its own tracks instead, because `width`/`height`
        describe a file and an AAF is not one — a sound-only export from Media
        Composer would otherwise look identical to a video whose probe had not
        run, and both would be given a V1 track the sequence cannot support.
        """
        if self.aaf is not None:
            return self.aaf.has_video
        return bool(self.width and self.height)


def warnings_for(assets: dict[str, AssetRef]) -> list[str]:
    """Things about this combination of assets the editor should be told.

    Returned rather than printed, and rather than raised: mixed rates are a real
    edit that a person may well want, and silently conforming them is worse than
    saying so.
    """
    out: list[str] = []
    if not assets:
        return out
    refs = list(assets.values())
    seq = refs[0].rate
    odd = [r for r in refs[1:] if (r.rate.num, r.rate.den) != (seq.num, seq.den)]
    if odd:
        others = ", ".join(sorted({str(r.rate) for r in odd}))
        out.append(
            f"mixed frame rates — sequence is {seq}, and {len(odd)} asset(s) "
            f"are {others}. Clip timings are correct at each asset's own rate, "
            f"but the NLE will conform them on import. Check the joins.")
    return out


def build_multi(cuts: list[Cut], assets: dict[str, AssetRef],
                name: str = "mishne_roughcut",
                record_start_hours: int = 1) -> otio.schema.Timeline:
    """Assemble cuts drawn from any number of assets into one timeline."""
    if not assets:
        raise ValueError("no assets to assemble from")

    refs = list(assets.values())
    seq_rate = refs[0].rate
    fps = seq_rate.fps

    timeline = otio.schema.Timeline(name=name)
    # Record timecode from 01:00:00:00, the broadcast convention. Drop-frame
    # aware: 01:00:00;00 is not the same frame as 01:00:00:00.
    timeline.global_start_time = RationalTime(
        tc_to_frames(record_start_hours, 0, 0, 0, seq_rate), fps)

    # A video track only where there is video to put on it.
    #
    # Media Composer follows a V1 clip back to its source and refuses the
    # sequence outright if that source has no picture track:
    #
    #     Exception: Sequence refers to non-existent track in clip.
    #     ..., clip:0bf16051-....wav, missingTrack:V1
    #
    # The whole sequence is rejected, so this is not a cosmetic empty track —
    # it is the difference between a deliverable and a dialog. An audio-only
    # ingest is a first-class path (ADR-0005) and a sound-only AAF export is
    # ordinary for a podcast or a radio piece, so this is the common case for
    # that material rather than an edge one.
    #
    # `has_picture` is the probe's answer, not a guess: an asset whose frame
    # geometry is unknown is audio, and an AAF built entirely from sound slots
    # reports no picture for the same reason. Where any asset in the job does
    # carry picture the track is built as before, because a mixed job still
    # needs somewhere to cut the picture.
    picture = any(r.has_picture for r in refs)
    tracks: list[otio.schema.Track] = []
    v_track = None
    if picture:
        v_track = otio.schema.Track(name="V1", kind=otio.schema.TrackKind.Video)
        timeline.tracks.append(v_track)
        tracks.append(v_track)
    a_tracks = []
    for i in range(max(1, max(r.audio_tracks for r in refs))):
        t = otio.schema.Track(name=f"A{i + 1}", kind=otio.schema.TrackKind.Audio)
        timeline.tracks.append(t)
        a_tracks.append(t)
    tracks.extend(a_tracks)

    warnings = warnings_for(assets)
    timeline.metadata["mishne"] = {
        "assets": [
            {"assetId": r.asset_id, "name": r.name, "rate": str(r.rate),
             "isAaf": r.aaf is not None} for r in refs],
        "warnings": warnings,
    }

    order = 0
    for cut in sorted(cuts, key=lambda c: c.order_idx):
        asset = assets.get(cut.asset_id)
        if asset is None:
            raise KeyError(
                f"cut {cut.beat_id} belongs to asset {cut.asset_id!r}, which "
                f"was not supplied to assembly")
        emit = _aaf_clips if asset.aaf is not None else _media_clips
        for spec in emit(cut, asset, seq_rate):
            for track in tracks:
                track.append(_clip(spec, cut, asset, order,
                                   picture=track is v_track))
            order += 1

    return timeline


# --- one cut, resolved to the spans of real media it covers ------------------
#
# Both resolvers yield the same shape: (source name, media reference factory,
# in frame, out frame, rate, extra metadata). Everything that differs between a
# plain file and an AAF sequence lives here; `_clip` below is shared.


def _media_clips(cut: Cut, asset: AssetRef, seq_rate: Rate):
    """A plain media asset: one cut is one span of one file."""
    fps = seq_rate.fps
    lo = _conform(asset.start_tc_frames, asset.rate, seq_rate)
    hi = _conform(asset.start_tc_frames + asset.duration_frames,
                  asset.rate, seq_rate)
    available = TimeRange(
        start_time=RationalTime(lo, fps),
        duration=RationalTime(hi - lo, fps),
    )
    filename = asset.filename
    # Identity is the media itself, not the path it sits at today and not the
    # name either — the customer will move it, and a worker will have staged it
    # under a sanitised one. The content hash is `asset_id` on every path that
    # has one; the filename remains the fallback for a caller that does not.
    identity = (f"mishne/{asset.asset_id}" if asset.asset_id
                else f"mishne/{filename}/{asset.duration_frames}")
    url = media_url(filename, None if asset.staged else asset.media_path)
    essence = _essence_description(asset)

    def make_ref():
        ref = otio.schema.ExternalReference(
            target_url=url, available_range=available)
        ref.name = Path(filename).stem
        mobid.attach(ref, identity)
        if essence:
            ref.metadata["AAF"]["EssenceDescription"] = dict(essence)
        # Read by `interchange/fcpx_patch`, which cannot probe a file it has
        # deliberately not been given a path to.
        ref.metadata["mishne"] = {"width": asset.width, "height": asset.height}
        return ref

    yield (Path(filename).stem, make_ref,
           _conform(cut.src_in, asset.rate, seq_rate),
           _conform(cut.src_out, asset.rate, seq_rate), seq_rate, {})


def media_url(filename: str, at: Path | None = None) -> str:
    """How an artifact names its media.

    `at` is the file's real location **only when that location is the
    customer's own** — `run.py` reading rushes off a laptop. Then the artifact
    says so, absolutely, and every NLE relinks with no dialog at all.

    A worker has no such path to offer. It staged a copy into a scratch
    directory under a sanitised name and deletes the directory when the job
    ends, so `at` is None and the answer is a bare, percent-encoded basename —
    a relative URL. Two things follow, and both are wanted:

    * **It resolves for free when the artifact sits beside the media.** FCPXML
      `src` and the AAF's `NetworkLocator` are both resolved relative to the
      document, so a customer who drops the export into the folder their rushes
      are in gets a silent relink.
    * **When it does not resolve, the NLE asks for a file by the right name.**
      Premiere's "locate the media", Resolve's relink and Avid's *relink by
      source file name* all match on the basename, and the basename is now the
      customer's own — apostrophes, commas and all.

    What a worker must never write is the third option, and it is what it used
    to write: an absolute `file://` URL to the scratch copy. Off the machine
    that ran the job that is worse than saying nothing — a confident statement
    about a path the customer has never had, under a filename their media does
    not have. Media that genuinely travels with the artifact belongs embedded,
    not linked.
    """
    from urllib.parse import quote

    if at is not None:
        return at.resolve().as_uri()
    # RFC 3986 `pchar`, so the result is a valid path segment while an ordinary
    # filename still reads as one. `quote`'s default safe set encodes every
    # sub-delimiter, which turns `'My Next Act,' (360p)` into a wall of `%27`
    # and `%28` in an EDL comment a person is meant to be able to read. Space,
    # `%`, `#` and `?` are still encoded, which is what actually matters.
    return quote(filename, safe="!$&'()*+,;=:@-._~")


def _essence_description(asset: AssetRef) -> dict:
    """Frame geometry for the AAF writer, when the probe found any.

    `otio_aaf_adapter` defaults an unknown picture descriptor to 1920x1080 16/9
    — silently, and for 640x360 media too. Avid believes the descriptor, so the
    master clip it builds on relink is the wrong shape. The adapter reads these
    keys out of the media reference if they are there, so give it the truth.
    """
    if not (asset.width and asset.height):
        return {}
    from math import gcd

    g = gcd(asset.width, asset.height) or 1
    return {
        "StoredWidth": asset.width,
        "StoredHeight": asset.height,
        "ImageAspectRatio": f"{asset.width // g}/{asset.height // g}",
    }


def _aaf_clips(cut: Cut, asset: AssetRef, seq_rate: Rate):
    """An AAF asset: one cut may cross a join and become several clips.

    Cuts arrive in the AAF's *timeline* coordinates, because the pipeline
    transcribed the flattened sequence. Each is mapped back through the source
    map to the actual clips it came from.

    **A cut that spans a join in the original sequence becomes two clips**, each
    pointing at its own source with its own mob ID. That is not a workaround —
    those frames genuinely come from different media, and collapsing them into
    one clip would produce a timeline that cannot resolve.

    Inheriting the original mob IDs is the whole point: the result relinks
    silently in the project the AAF came from.
    """
    source = asset.aaf
    fps = seq_rate.fps
    extents = _extents(asset)

    tl_in = cut.src_in - source.start_tc_frames
    tl_out = cut.src_out - source.start_tc_frames

    for clip, src_in_units, src_out_units in map_to_source(
            source, tl_in, tl_out):
        # Two conversions, and they are not the same one. `_to_frames` turns
        # the AAF's native units — 48 kHz samples, routinely — into frames;
        # `_conform` then moves those frames onto the sequence rate.
        src_in = _conform(_to_frames(src_in_units, clip.src_rate,
                                     asset.rate.fps), asset.rate, seq_rate)
        src_out = _conform(_to_frames(src_out_units, clip.src_rate,
                                      asset.rate.fps), asset.rate, seq_rate)
        if src_out <= src_in:
            continue
        ext_lo, ext_hi = extents[clip.mob_id or clip.name]
        ext_lo = _conform(ext_lo, asset.rate, seq_rate)
        ext_hi = _conform(ext_hi, asset.rate, seq_rate)

        def make_ref(clip=clip, ext_lo=ext_lo, ext_hi=ext_hi):
            if clip.media_path is not None:
                # Same rule as `_media_clips`, and one extra wrinkle. A staged
                # companion's path is this worker's scratch directory and
                # belongs in no artifact — but its *basename* on disk has been
                # through `_safe_name` too, so it is not the customer's name
                # either. The source AAF's own locator is, and it is the one
                # record of it we have; `media_path.name` is the fallback for a
                # clip whose locator was empty and which resolved by some other
                # route. The mob ID below remains the real relink key here.
                ref = otio.schema.ExternalReference(
                    target_url=media_url(
                        basename_of(clip.target_url) or clip.media_path.name,
                        None if asset.staged else clip.media_path))
            else:
                ref = otio.schema.MissingReference()
            ref.name = clip.name
            ref.available_range = TimeRange(
                start_time=RationalTime(ext_lo, fps),
                duration=RationalTime(ext_hi - ext_lo, fps))
            # The AAF's own mob ID, not a synthesised one. This is what makes
            # the output relink without a dialog.
            if clip.mob_id:
                meta = ref.metadata.setdefault("AAF", {})
                meta["MobID"] = clip.mob_id
                meta["SourceID"] = clip.mob_id
            else:
                mobid.attach(ref, f"mishne/{clip.name}")
            return ref

        yield (clip.name, make_ref, src_in, src_out, seq_rate,
               {"source_clip": clip.index})


def _clip(spec, cut: Cut, asset: AssetRef, order: int,
          picture: bool = True) -> otio.schema.Clip:
    """The part that is the same however the source was resolved."""
    src_name, make_ref, src_in, src_out, rate, extra = spec
    fps = rate.fps
    ref = make_ref()
    if not picture:
        # Frame geometry describes an image and the AAF writer builds a
        # PCMDescriptor for an audio track, so handing it StoredWidth means a
        # KeyError logged once per key per clip and nothing else.
        ref.metadata.get("AAF", {}).pop("EssenceDescription", None)
    clip = otio.schema.Clip(
        name=f"{src_name}_{order + 1:03d}",
        media_reference=ref,
        source_range=TimeRange(
            start_time=RationalTime(src_in, fps),
            duration=RationalTime(src_out - src_in, fps)),
    )
    # Reel name is the fallback relink key; EDL has nothing else.
    clip.metadata.setdefault("cmx_3600", {})["reel"] = src_name[:8]
    clip.metadata["mishne"] = {
        "beat_id": cut.beat_id,
        "asset_id": cut.asset_id,
        "speaker": cut.speaker,
        "score": round(cut.score, 1),
        "rationale": cut.rationale,
        "warnings": cut.warnings,
        **extra,
    }
    return clip


def _conform(frames: int, from_rate: Rate, to_rate: Rate) -> int:
    """A frame number at one rate, as the same instant at another.

    Frame 100 of a 25 fps reel is four seconds in; at 23.976 that instant is
    frame 95.9, and 96 is the honest answer. Conforming the *number* instead
    would move the clip by a fifth of a second and nothing downstream would
    say so.

    Applied to source positions and to media extents alike — they have to agree
    or the clip resolves outside its own media. A no-op when the rates match,
    which is every single-asset job.
    """
    if (from_rate.num, from_rate.den) == (to_rate.num, to_rate.den):
        return frames
    return round(frames / from_rate.fps * to_rate.fps)


def _to_frames(units: int, src_rate: float, fps: float) -> int:
    """Source positions are converted to SEQUENCE FRAMES here.

    Production audio arrives at 48 kHz, so a clip's source range is in samples.
    Carrying that into the output looks fine in AAF and FCPXML — both
    round-trip it happily — and then EDL fails, because CMX3600 is frame-based
    and cannot express a sample position at all. The first attempt produced
    source durations of -1,127,040 frames.

    A rough cut is a video edit: stage 9 has already quantised every cut point
    to frames and handles, where a delivery asks for them, are frame-quantised too. Sub-frame audio precision is
    not meaningful here, and frame positions are the only thing all four
    formats agree on.
    """
    return round(units / src_rate * fps) if src_rate else int(units)


def _extents(asset: AssetRef) -> dict:
    """One media extent per mob ID, covering everything any clip uses of it.

    An AAF holds one source mob per MobID. Several output clips routinely
    reference the same source, and if each declares a different
    `available_range` the writer cannot reconcile them — it keeps one and every
    other clip's start offset comes back wrong. Durations survive, positions do
    not, which makes it look like a timecode bug rather than a structural one.
    The validation gate caught exactly this.
    """
    if asset._extents is not None:
        return asset._extents
    # At the asset's OWN rate. Conforming to the sequence happens at the call
    # site, alongside the source positions, so the two can never diverge.
    fps = asset.rate.fps
    out: dict = {}
    for c in asset.aaf.primary_clips:
        key = c.mob_id or c.name
        lo_f = _to_frames(c.src_in, c.src_rate, fps)
        hi_f = _to_frames(c.src_out, c.src_rate, fps)
        lo, hi = out.get(key, (lo_f, hi_f))
        out[key] = (min(lo, lo_f), max(hi, hi_f))
    asset._extents = out
    return out


# --- single-asset entry points ----------------------------------------------
#
# Kept as the callers know them, and implemented as a map of one so there is
# only ever one assembly path to get right.


def build(cuts: list[Cut], media_path: Path, rate: Rate,
          source_start_frames: int, source_duration_frames: int,
          audio_tracks: int = 1, name: str = "mishne_roughcut",
          record_start_hours: int = 1) -> otio.schema.Timeline:
    """Assemble cuts into an OTIO timeline referencing the original media."""
    aid = cuts[0].asset_id if cuts else ""
    asset = AssetRef(
        rate=rate, start_tc_frames=source_start_frames,
        duration_frames=source_duration_frames, asset_id=aid,
        media_path=media_path, audio_tracks=audio_tracks)
    return build_multi(cuts, {aid: asset}, name=name,
                       record_start_hours=record_start_hours)


def build_from_aaf(cuts: list[Cut], source: AAFSource,
                   name: str = "mishne_roughcut",
                   record_start_hours: int = 1) -> otio.schema.Timeline:
    """Assemble a timeline from an AAF source, referencing the originals."""
    aid = cuts[0].asset_id if cuts else ""
    asset = AssetRef(
        rate=source.rate, start_tc_frames=source.start_tc_frames,
        duration_frames=source.duration_frames, asset_id=aid, aaf=source,
        audio_tracks=1)
    return build_multi(cuts, {aid: asset}, name=name,
                       record_start_hours=record_start_hours)
