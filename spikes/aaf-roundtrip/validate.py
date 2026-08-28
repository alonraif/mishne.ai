"""Round-trip validation.

Re-parse every written file and diff it against the timeline that produced it.
This is the gate described in docs/architecture/02 — a few hours of work that
catches the entire class of "the export looked fine but Avid disagrees".

What it can prove: the file parses, and the structure and timings survived.
What it cannot prove: that Media Composer will open it. Only Media Composer can
prove that, which is why checklist.py exists.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

import opentimelineio as otio


@dataclass
class Check:
    name: str
    ok: bool
    detail: str = ""


@dataclass
class RoundTrip:
    fmt: str
    parsed: bool
    checks: list[Check] = field(default_factory=list)
    error: str = ""

    @property
    def ok(self) -> bool:
        return self.parsed and all(c.ok for c in self.checks)


def _ranges_by_kind(timeline: otio.schema.Timeline) -> dict[str, list[tuple]]:
    """(start frame, duration frames) per clip, grouped by track kind."""
    out: dict[str, list[tuple]] = {}
    for track in timeline.tracks:
        bucket = out.setdefault(str(track.kind), [])
        for clip in track.find_clips():
            sr = clip.source_range
            bucket.append((round(sr.start_time.value), round(sr.duration.value)))
    return out


def verify(original: otio.schema.Timeline, path: Path, fmt: str,
           adapter: str, read_kwargs: dict | None = None,
           flattens_audio: bool = False) -> RoundTrip:
    """Re-parse `path` and compare against `original`.

    `read_kwargs` exists because **an EDL does not carry its own frame rate**.
    CMX3600 is a text format from the tape era: it holds timecode and nothing
    that says what those timecodes mean. The reader defaults to 24 and silently
    misreads everything.

    That is a property of the format, not a bug in the adapter, and it has a
    product consequence: an EDL handed to a customer is ambiguous unless they
    already know the rate. Put the rate in the filename, and prefer AAF or
    FCPXML wherever the customer's NLE will take one.

    `flattens_audio` is the other EDL concession. CMX3600 expresses audio as
    channel notation on the video event (A1/A2/AA) rather than as separate
    clips, so audio clips do not survive as countable objects. Comparing them
    would fail a format that is behaving correctly, so video is the strict
    check and audio is reported for information.
    """
    try:
        back = otio.adapters.read_from_file(
            str(path), adapter_name=adapter, **(read_kwargs or {})
        )
    except Exception as exc:  # noqa: BLE001
        return RoundTrip(fmt, False, error=f"{type(exc).__name__}: {exc}")

    rt = RoundTrip(fmt, True)
    orig = _ranges_by_kind(original)
    back_r = _ranges_by_kind(back)

    video_kind = str(otio.schema.TrackKind.Video)
    audio_kind = str(otio.schema.TrackKind.Audio)

    o_vid = sorted(orig.get(video_kind, []))
    b_vid = sorted(back_r.get(video_kind, []))

    rt.checks.append(Check(
        "video clips", len(o_vid) == len(b_vid),
        f"wrote {len(o_vid)}, read {len(b_vid)}",
    ))

    if o_vid == b_vid:
        rt.checks.append(Check("source ranges", True, "frame-exact"))
    else:
        # Multiset comparison — a plain set hides duplicate ranges, and the cut
        # plan deliberately contains duplicates.
        oc, bc = Counter(o_vid), Counter(b_vid)
        missing = list((oc - bc).elements())[:3]
        extra = list((bc - oc).elements())[:3]
        rt.checks.append(Check(
            "source ranges", False,
            f"missing {missing}" + (f", unexpected {extra}" if extra else ""),
        ))

    o_dur, b_dur = sum(d for _s, d in o_vid), sum(d for _s, d in b_vid)
    rt.checks.append(Check(
        "video duration", o_dur == b_dur, f"{o_dur} vs {b_dur} frames"))

    o_aud = sorted(orig.get(audio_kind, []))
    b_aud = sorted(back_r.get(audio_kind, []))
    if flattens_audio:
        rt.checks.append(Check(
            "audio tracks", True,
            f"{len(b_aud)} read — format expresses audio as channel notation, "
            "not as clips",
        ))
    else:
        rt.checks.append(Check(
            "audio clips", o_aud == b_aud,
            f"wrote {len(o_aud)}, read {len(b_aud)}"
            + ("" if o_aud == b_aud else " — ranges differ"),
        ))

    o_rate, b_rate = original.duration().rate, back.duration().rate
    rt.checks.append(Check(
        "edit rate", abs(o_rate - b_rate) < 1e-6,
        f"{o_rate:.4f} vs {b_rate:.4f}"))

    return rt
