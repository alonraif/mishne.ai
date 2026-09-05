"""Stage 11 — generate the deliverables.

Every writer is attempted independently and failures are captured rather than
raised: a job that produces three of four formats is worth delivering, and
knowing which one failed is worth more than a stack trace.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import opentimelineio as otio

from ...interchange import fcpx_patch

# (label, adapter, extension, target NLEs, flattens audio into channel notation,
#  needs a video track to write at all)
#
# The last column is about the *writers*, not about the edit. `cmx_3600` refuses
# a timeline with no video track outright ("Only a single video track is
# supported, got: 0") and `fcpx_xml` dies reaching into one it assumes is there.
# An audio-only cut is an ordinary deliverable — a podcast, a radio piece, any
# sound-only AAF export — so for those two a picture track is synthesised at
# write time.
#
# It is emphatically NOT synthesised for the AAF. Media Composer follows a V1
# clip to its source and rejects the whole sequence when that source has no
# picture ("Sequence refers to non-existent track in clip ... missingTrack:V1"),
# which is the bug this column exists to keep fixed. The two requirements are
# opposite, which is why this is a property of the format rather than of the
# timeline.
FORMATS = [
    ("AAF", "AAF", "aaf", "Avid Media Composer", False, False),
    ("FCPXML", "fcpx_xml", "fcpxml", "Premiere Pro · Resolve · Final Cut", False, True),
    ("EDL", "cmx_3600", "edl", "Universal fallback", True, True),
    ("OTIO", "otio_json", "otio", "Canonical", False, False),
]


@dataclass
class Artifact:
    #: The label a person reads — "FCPXML". Stage 12 also keys `FORMATS` on it.
    fmt: str
    path: Path | None
    ok: bool
    bytes: int = 0
    target_nle: str = ""
    error: str = ""
    #: The same format as an identifier — the file extension, lower case. This
    #: is what `artifacts.kind` and `ArtifactKind` are, and it is a separate
    #: field from `fmt` on purpose: while the label was also the identifier,
    #: every artifact row the orchestrator wrote was rejected by
    #: `ck_artifacts_kind` and every completed job failed after producing its
    #: deliverables. A display string and a stored key are allowed to diverge —
    #: they must not share one attribute.
    kind: str = ""


def emit(timeline: otio.schema.Timeline, out_dir: Path,
         stem: str) -> list[Artifact]:
    out_dir.mkdir(parents=True, exist_ok=True)
    # NTSC rates cannot be written without this. See interchange/fcpx_patch.py.
    fcpx_patch.apply()

    results: list[Artifact] = []
    for label, adapter, ext, nle, _flat, needs_video in FORMATS:
        path = out_dir / f"{stem}.{ext}"
        writable = timeline
        if needs_video and not has_video(timeline):
            writable = with_synthetic_video(timeline)
        try:
            otio.adapters.write_to_file(writable, str(path), adapter_name=adapter)
            results.append(Artifact(label, path, True, path.stat().st_size, nle,
                                    kind=ext))
        except Exception as exc:  # noqa: BLE001
            results.append(Artifact(label, None, False, target_nle=nle,
                                    error=f"{type(exc).__name__}: {exc}",
                                    kind=ext))
    return results


def has_video(timeline: otio.schema.Timeline) -> bool:
    return any(t.kind == otio.schema.TrackKind.Video for t in timeline.tracks)


def with_synthetic_video(timeline: otio.schema.Timeline) -> otio.schema.Timeline:
    """A copy of an audio-only timeline with a picture track mirroring A1.

    For the two writers that cannot express a cut without one. The clips are
    copies of the audio track's, so every event has the same source range and
    the same reel name, and a round-trip still compares frame for frame.

    A copy, never the original: the AAF is written from the same timeline object
    and a V1 track on it is the failure this exists to avoid.

    Public because stage 12 needs the same timeline to compare against. Verifying
    an EDL written from this against the audio-only original would report every
    event as missing — the artifact would be correct and the check would call it
    broken, which is the worse failure of the two.
    """
    copy = timeline.deepcopy()
    audio = next((t for t in copy.tracks
                  if t.kind == otio.schema.TrackKind.Audio), None)
    if audio is None:
        return copy
    video = otio.schema.Track(name="V1", kind=otio.schema.TrackKind.Video)
    for child in audio:
        video.append(child.deepcopy())
    copy.tracks.insert(0, video)
    return copy
