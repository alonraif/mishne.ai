"""Round-trip validation shared types, and the OTIO-based checker.

The gate: every generated artifact is re-parsed and diffed against the canonical
OTIO before a job is marked complete. Mismatch fails the job.

Shipping a subtly wrong AAF to a broadcast editor costs more trust than failing
loudly ever will, and this gate is a few hours of work that prevents the entire
class of "the export looked fine but Avid disagrees".
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
    out: dict[str, list[tuple]] = {}
    for track in timeline.tracks:
        bucket = out.setdefault(str(track.kind), [])
        for clip in track.find_clips():
            sr = clip.source_range
            bucket.append((round(sr.start_time.value), round(sr.duration.value)))
    return out


def verify(original: otio.schema.Timeline, path: Path, fmt: str, adapter: str,
           read_kwargs: dict | None = None,
           flattens_audio: bool = False) -> RoundTrip:
    """Re-parse `path` and compare against `original`.

    `read_kwargs` exists because an EDL carries no frame rate — CMX3600 holds
    timecode and nothing that says what those timecodes mean, so the reader
    defaults to 24 and silently misreads everything.

    `flattens_audio` is EDL's other concession: it expresses audio as channel
    notation on the video event rather than as separate clips, so audio does not
    survive as countable objects. Video is the strict check; audio is reported.
    """
    try:
        back = otio.adapters.read_from_file(
            str(path), adapter_name=adapter, **(read_kwargs or {}))
    except Exception as exc:  # noqa: BLE001
        return RoundTrip(fmt, False, error=f"{type(exc).__name__}: {exc}")

    rt = RoundTrip(fmt, True)
    orig, got = _ranges_by_kind(original), _ranges_by_kind(back)
    vk = str(otio.schema.TrackKind.Video)
    ak = str(otio.schema.TrackKind.Audio)

    o_vid, b_vid = sorted(orig.get(vk, [])), sorted(got.get(vk, []))
    rt.checks.append(Check("video clips", len(o_vid) == len(b_vid),
                           f"wrote {len(o_vid)}, read {len(b_vid)}"))

    if o_vid == b_vid:
        rt.checks.append(Check("source ranges", True, "frame-exact"))
    else:
        oc, bc = Counter(o_vid), Counter(b_vid)
        rt.checks.append(Check(
            "source ranges", False,
            f"missing {list((oc - bc).elements())[:3]}"))

    o_dur, b_dur = sum(d for _s, d in o_vid), sum(d for _s, d in b_vid)
    rt.checks.append(Check("video duration", o_dur == b_dur,
                           f"{o_dur} vs {b_dur} frames"))

    o_aud, b_aud = sorted(orig.get(ak, [])), sorted(got.get(ak, []))
    if flattens_audio:
        rt.checks.append(Check(
            "audio tracks", True,
            f"{len(b_aud)} read — format uses channel notation, not clips"))
    else:
        rt.checks.append(Check("audio clips", o_aud == b_aud,
                               f"wrote {len(o_aud)}, read {len(b_aud)}"))

    o_rate, b_rate = original.duration().rate, back.duration().rate
    rt.checks.append(Check("edit rate", abs(o_rate - b_rate) < 1e-6,
                           f"{o_rate:.4f} vs {b_rate:.4f}"))
    return rt
