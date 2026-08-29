"""Stage 10 — assemble the canonical timeline.

Builds one OpenTimelineIO document. **This is the record of truth for what the
edit was.** Every output format is a projection of it, no format is ever
generated from another, and a support engineer debugging a bad export starts
here. See docs/adr/0001-otio-as-canonical-timeline.md.
"""

from __future__ import annotations

from pathlib import Path

import opentimelineio as otio
from opentimelineio.opentime import RationalTime, TimeRange

from ...interchange import mobid
from ...timecode import Rate, tc_to_frames
from .aaf_ingest import AAFSource, map_to_source
from .refine import Cut


def build(cuts: list[Cut], media_path: Path, rate: Rate,
          source_start_frames: int, source_duration_frames: int,
          audio_tracks: int = 1, name: str = "mishne_roughcut",
          record_start_hours: int = 1) -> otio.schema.Timeline:
    """Assemble cuts into an OTIO timeline referencing the original media."""
    fps = rate.fps
    timeline = otio.schema.Timeline(name=name)
    # Record timecode from 01:00:00:00, the broadcast convention. Drop-frame
    # aware: 01:00:00;00 is not the same frame as 01:00:00:00.
    timeline.global_start_time = RationalTime(
        tc_to_frames(record_start_hours, 0, 0, 0, rate), fps)

    v_track = otio.schema.Track(name="V1", kind=otio.schema.TrackKind.Video)
    timeline.tracks.append(v_track)
    a_tracks = []
    for i in range(max(1, audio_tracks)):
        t = otio.schema.Track(name=f"A{i + 1}", kind=otio.schema.TrackKind.Audio)
        timeline.tracks.append(t)
        a_tracks.append(t)

    available = TimeRange(
        start_time=RationalTime(source_start_frames, fps),
        duration=RationalTime(source_duration_frames, fps),
    )
    # Identity is the media itself, not the path it sits at today — the customer
    # will move it. A content hash would be better; the filename is the workable
    # approximation.
    identity = f"mishne/{media_path.name}/{source_duration_frames}"

    for cut in sorted(cuts, key=lambda c: c.order_idx):
        for track in [v_track, *a_tracks]:
            ref = otio.schema.ExternalReference(
                target_url=media_path.resolve().as_uri(),
                available_range=available,
            )
            ref.name = media_path.stem
            mobid.attach(ref, identity)

            clip = otio.schema.Clip(
                name=f"{media_path.stem}_{cut.order_idx + 1:03d}",
                media_reference=ref,
                source_range=TimeRange(
                    start_time=RationalTime(cut.src_in, fps),
                    duration=RationalTime(cut.frames, fps),
                ),
            )
            # Reel name is the fallback relink key; EDL has nothing else.
            clip.metadata.setdefault("cmx_3600", {})["reel"] = media_path.stem[:8]
            clip.metadata["mishne"] = {
                "beat_id": cut.beat_id,
                "speaker": cut.speaker,
                "score": round(cut.score, 1),
                "rationale": cut.rationale,
                "warnings": cut.warnings,
            }
            track.append(clip)

    return timeline


def build_from_aaf(cuts: list[Cut], source: AAFSource,
                   name: str = "mishne_roughcut",
                   record_start_hours: int = 1) -> otio.schema.Timeline:
    """Assemble a timeline from an AAF source, referencing the originals.

    Cuts arrive in *timeline* coordinates, because the pipeline transcribed the
    flattened sequence. Each is mapped back through the source map to the actual
    clips it came from.

    **A cut that spans a join in the original sequence becomes two clips**, each
    pointing at its own source with its own mob ID. That is not a workaround —
    those frames genuinely come from different media, and collapsing them into
    one clip would produce a timeline that cannot resolve.

    Inheriting the original mob IDs is the whole point: the result relinks
    silently in the project the AAF came from.
    """
    fps = source.rate.fps
    timeline = otio.schema.Timeline(name=name)
    timeline.global_start_time = RationalTime(
        tc_to_frames(record_start_hours, 0, 0, 0, source.rate), fps)

    v_track = otio.schema.Track(name="V1", kind=otio.schema.TrackKind.Video)
    a_track = otio.schema.Track(name="A1", kind=otio.schema.TrackKind.Audio)
    timeline.tracks.append(v_track)
    timeline.tracks.append(a_track)

    # available_range describes the MEDIA, not the clip.
    #
    # An AAF holds one source mob per MobID. Several output clips routinely
    # reference the same source, and if each declares a different
    # available_range the writer cannot reconcile them — it keeps one and every
    # other clip's start offset comes back wrong. Durations survive, positions
    # do not, which makes it look like a timecode bug rather than a structural
    # one. The validation gate caught exactly this.
    #
    # So: one extent per MobID, covering everything any clip uses of it.
    extents: dict[str, tuple[int, int]] = {}
    for c in source.clips:
        key = c.mob_id or c.name
        lo, hi = extents.get(key, (c.src_in, c.src_out))
        extents[key] = (min(lo, c.src_in), max(hi, c.src_out))

    order = 0
    for cut in sorted(cuts, key=lambda c: c.order_idx):
        tl_in = cut.src_in - source.start_tc_frames
        tl_out = cut.src_out - source.start_tc_frames

        for clip, src_in, src_out in map_to_source(source, tl_in, tl_out):
            if src_out <= src_in:
                continue
            for track in (v_track, a_track):
                if clip.media_path is not None:
                    ref = otio.schema.ExternalReference(
                        target_url=clip.media_path.resolve().as_uri())
                else:
                    ref = otio.schema.MissingReference()
                ref.name = clip.name
                ext_lo, ext_hi = extents[clip.mob_id or clip.name]
                ref.available_range = TimeRange(
                    start_time=RationalTime(ext_lo, fps),
                    duration=RationalTime(ext_hi - ext_lo, fps))

                # The AAF's own mob ID, not a synthesised one. This is what
                # makes the output relink without a dialog.
                if clip.mob_id:
                    meta = ref.metadata.setdefault("AAF", {})
                    meta["MobID"] = clip.mob_id
                    meta["SourceID"] = clip.mob_id
                else:
                    mobid.attach(ref, f"mishne/{clip.name}")

                otio_clip = otio.schema.Clip(
                    name=f"{clip.name}_{order + 1:03d}",
                    media_reference=ref,
                    source_range=TimeRange(
                        start_time=RationalTime(src_in, fps),
                        duration=RationalTime(src_out - src_in, fps)),
                )
                otio_clip.metadata.setdefault("cmx_3600", {})["reel"] = \
                    clip.name[:8]
                otio_clip.metadata["mishne"] = {
                    "beat_id": cut.beat_id,
                    "speaker": cut.speaker,
                    "score": round(cut.score, 1),
                    "rationale": cut.rationale,
                    "source_clip": clip.index,
                    "warnings": cut.warnings,
                }
                track.append(otio_clip)
            order += 1

    return timeline
