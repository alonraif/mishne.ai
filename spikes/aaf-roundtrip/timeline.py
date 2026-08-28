"""Build the OTIO timeline.

This is the shape mishne.ai's stage 10 will produce, minus the part where the
cuts come from a solver rather than a fixture. Getting this right here means
stage 10 is already proven when it gets written.
"""

from __future__ import annotations

from pathlib import Path

import opentimelineio as otio
from opentimelineio.opentime import RationalTime, TimeRange

import mobid
from timecode import tc_to_frames
from rates import CUT_PLAN, SOURCE_DURATION_S, SOURCE_START_TC, Cut, Rate


def start_frames(rate: Rate) -> int:
    """Source start timecode as an absolute frame count.

    Drop-frame aware — see timecode.py. Getting this wrong by using plain
    label arithmetic is what produced `09:58:35;28` for `09:58:00;00` in the
    first draft of this spike.
    """
    return tc_to_frames(*SOURCE_START_TC, rate)


def to_frames(seconds: float, rate: Rate) -> int:
    return round(seconds * rate.fps)


def build(rate: Rate, media_path: Path, handle_frames: int = 6,
          cuts: list[Cut] | None = None) -> otio.schema.Timeline:
    """Assemble a timeline from the cut plan.

    One video track and one audio track, clips referencing the source by
    timecode. Handles are added on both sides of every cut, which is what makes
    the result a rough cut rather than a locked one — the editor needs material
    to trim with.
    """
    cuts = cuts or CUT_PLAN
    fps = rate.fps

    timeline = otio.schema.Timeline(name=f"MISHNE_SPIKE_{rate.key}")
    # Record timecode starts at 01:00:00:00, the broadcast convention.
    timeline.global_start_time = RationalTime(tc_to_frames(1, 0, 0, 0, rate), fps)

    v_track = otio.schema.Track(name="V1", kind=otio.schema.TrackKind.Video)
    a_track = otio.schema.Track(name="A1", kind=otio.schema.TrackKind.Audio)
    timeline.tracks.append(v_track)
    timeline.tracks.append(a_track)

    src_start = start_frames(rate)
    src_len = to_frames(SOURCE_DURATION_S, rate)
    available = TimeRange(
        start_time=RationalTime(src_start, fps),
        duration=RationalTime(src_len, fps),
    )

    # Identity is the media's own name, not the path it sits at today.
    identity = f"mishne-spike/{media_path.name}"

    for idx, cut in enumerate(cuts):
        raw_in = src_start + to_frames(cut.offset_s, rate)
        raw_dur = to_frames(cut.dur_s, rate)

        # Handles, clamped to what the media actually has. A handle that runs
        # off the end of the source produces a clip the NLE cannot resolve.
        head = min(handle_frames, raw_in - src_start)
        tail = min(handle_frames, (src_start + src_len) - (raw_in + raw_dur))
        tail = max(0, tail)
        head = max(0, head)

        in_frame = raw_in - head
        duration = raw_dur + head + tail

        for track in (v_track, a_track):
            ref = otio.schema.ExternalReference(
                target_url=media_path.resolve().as_uri(),
                available_range=available,
            )
            ref.name = media_path.stem
            mobid.attach(ref, identity)

            clip = otio.schema.Clip(
                name=f"{media_path.stem}_{idx + 1:02d}",
                media_reference=ref,
                source_range=TimeRange(
                    start_time=RationalTime(in_frame, fps),
                    duration=RationalTime(duration, fps),
                ),
            )
            # Reel/tape name is the fallback relink key when MobID is not
            # available — EDL has nothing else to go on.
            clip.metadata.setdefault("cmx_3600", {})["reel"] = media_path.stem[:8]
            clip.metadata["mishne"] = {
                "cut_index": idx,
                "why": cut.why,
                "handle_head": head,
                "handle_tail": tail,
            }
            track.append(clip)

    return timeline
