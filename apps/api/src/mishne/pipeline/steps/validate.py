"""Stage 12 — the validation gate.

Re-parse every generated artifact and diff it against the canonical OTIO. A
mismatch fails the job rather than delivering the file.

FCPXML is checked by parsing the XML directly rather than through the OTIO
reader. The immediate reason is that the reader truncates NTSC rates to an
integer and reads those files ~4% wrong while the written file is correct. The
general reason matters more: **validating a file by reading it back with the
library that wrote it cannot catch a symmetric bug.** If writer and reader share
a wrong assumption the round trip agrees with itself and the gate passes a file
no NLE can open.
"""

from __future__ import annotations

from pathlib import Path

import opentimelineio as otio

from ...interchange import fcpxml_check
from ...interchange.validate import RoundTrip, verify
from ...timecode import Rate
from .emit import Artifact, FORMATS, has_video, with_synthetic_video


def validate(timeline: otio.schema.Timeline, artifacts: list[Artifact],
             rate: Rate) -> list[RoundTrip]:
    by_fmt = {f[0]: (f[1], f[4], f[5]) for f in FORMATS}
    results: list[RoundTrip] = []
    # The timeline the picture-requiring writers were actually given. Comparing
    # their output against the audio-only original would report every event as
    # missing and fail an artifact that is correct.
    audio_only = not has_video(timeline)
    synthetic = with_synthetic_video(timeline) if audio_only else timeline

    for art in artifacts:
        if not art.ok or art.path is None:
            continue
        adapter, flattens, needs_video = by_fmt[art.fmt]
        against = synthetic if (needs_video and audio_only) else timeline
        if art.fmt == "FCPXML":
            results.append(fcpxml_check.verify(against, art.path, rate))
            continue
        # EDL has no embedded frame rate; the reader defaults to 24 without this.
        kwargs = {"rate": rate.fps} if art.fmt == "EDL" else {}
        results.append(verify(against, art.path, art.fmt, adapter,
                              kwargs, flattens))
    return results
