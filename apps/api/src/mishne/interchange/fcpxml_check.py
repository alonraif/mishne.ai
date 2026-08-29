"""Independent FCPXML verification.

Two reasons this exists rather than reusing the OTIO reader.

**The specific reason.** `otio-fcpx-xml-adapter` truncates the frame rate to an
integer when reading:

    total, rate = format_element.get("frameDuration").split("/")
    return int(float(rate) / float(total))          # int(23.976...) -> 23

At 23.976 and 29.97 the reader therefore reports 23 and 29, and every timing it
returns is scaled by roughly 4%. The *written* file is correct — frameDuration
is a proper rational, the offsets are exact — so the deliverable is fine and
only the round-trip check is broken. Validating with the reader would fail a
good file.

**The general reason, which matters more.** Validating a file by reading it back
with the same library that wrote it cannot catch a symmetric bug. If the writer
and reader share a wrong assumption, the round trip agrees with itself and the
gate passes a file no NLE can open. Parsing the XML independently and comparing
against the source timeline is a real check; a round trip through one library is
a weaker one.

The same argument applies to the stage-12 validation gate in the product. See
docs/architecture/02-media-and-interchange.md.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from fractions import Fraction
from pathlib import Path

import opentimelineio as otio

from ..timecode import Rate
from .validate import Check, RoundTrip


def _seconds(value: str) -> Fraction:
    """FCPXML times are rationals with a trailing 's': '1001/24000s', '0s'."""
    v = value.strip().rstrip("s")
    return Fraction(v) if "/" in v else Fraction(int(v), 1)


def verify(original: otio.schema.Timeline, path: Path, rate: Rate) -> RoundTrip:
    rt = RoundTrip("FCPXML", True)

    try:
        root = ET.parse(path).getroot()
    except Exception as exc:  # noqa: BLE001
        return RoundTrip("FCPXML", False, error=f"{type(exc).__name__}: {exc}")

    expected_fd = Fraction(rate.den, rate.num)
    formats = list(root.iter("format"))
    fds = {_seconds(f.get("frameDuration", "0s")) for f in formats}
    rt.checks.append(Check(
        "frameDuration",
        fds == {expected_fd},
        f"{[str(f) for f in fds]} vs expected {expected_fd}",
    ))

    spine = root.find(".//spine")
    items = [c for c in spine if c.tag in ("clip", "asset-clip", "ref-clip")] if spine is not None else []

    v_track = next(t for t in original.tracks
                   if t.kind == otio.schema.TrackKind.Video)
    orig_clips = list(v_track.find_clips())

    rt.checks.append(Check(
        "clip count", len(items) == len(orig_clips),
        f"wrote {len(orig_clips)}, file has {len(items)}",
    ))

    # Compare every clip's source start and duration, converted back to frames.
    fps = Fraction(rate.num, rate.den)
    mismatches = []
    for i, (elem, clip) in enumerate(zip(items, orig_clips)):
        want_start = round(clip.source_range.start_time.value)
        want_dur = round(clip.source_range.duration.value)
        got_start = int(_seconds(elem.get("start", "0s")) * fps)
        got_dur = int(_seconds(elem.get("duration", "0s")) * fps)
        if (got_start, got_dur) != (want_start, want_dur):
            mismatches.append(
                f"#{i + 1} want {want_start}+{want_dur}, got {got_start}+{got_dur}"
            )

    rt.checks.append(Check(
        "source ranges", not mismatches,
        "frame-exact" if not mismatches
        else f"{len(mismatches)} differ: {mismatches[:2]}",
    ))

    total_file = sum(_seconds(e.get("duration", "0s")) for e in items) * fps
    total_orig = sum(round(c.source_range.duration.value) for c in orig_clips)
    rt.checks.append(Check(
        "total duration", int(total_file) == total_orig,
        f"{int(total_file)} vs {total_orig} frames",
    ))

    return rt
