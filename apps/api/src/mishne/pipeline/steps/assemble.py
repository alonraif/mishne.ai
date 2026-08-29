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
