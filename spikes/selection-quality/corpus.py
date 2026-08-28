"""Loading a ground-truth pair.

## Ground truth is free

The expensive-looking part of this spike is getting labelled data: someone
sitting down and marking which parts of three hours "should" be in the cut. Do
not do that. It is slow, it is one person's opinion, and it is not what the
product will be judged against.

**Every finished piece already carries its own ground truth.** The editor's
sequence — exported as an EDL, AAF or XML, which every NLE does in one menu
command — is an exact record of which source timecode ranges made the cut. That
is a better label than anyone would produce by hand, because it is what actually
went to air.

So a corpus entry is two things the customer already has:

    raw source (or just its audio)  +  the finished cut's EDL/AAF/XML

and nothing has to be annotated. This also means the metric can keep running
after launch on every hybrid job — the diff between what the engine proposed and
what the editor shipped is the same measurement. See
[ADR-0007](../../docs/adr/0007-selection-as-a-swappable-stage.md).

## Fixture format

For development, a pair can also be a single JSON file: beats with timings and
text, plus the human's selected source ranges. See `fixtures/`.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from metrics import Interval, normalize


@dataclass
class Beat:
    id: str
    start: int          # source frames
    end: int
    speaker: str
    text: str
    flags: list[str] = field(default_factory=list)

    @property
    def frames(self) -> int:
        return self.end - self.start

    @property
    def interval(self) -> Interval:
        return (self.start, self.end)


@dataclass
class Pair:
    """One piece of material plus the cut a human actually made from it."""

    name: str
    fps: float
    beats: list[Beat]
    human: list[Interval]
    notes: str = ""

    @property
    def human_frames(self) -> int:
        return sum(e - s for s, e in normalize(self.human))

    @property
    def source_frames(self) -> int:
        return max((b.end for b in self.beats), default=0) - min(
            (b.start for b in self.beats), default=0
        )

    def human_beats(self, min_overlap: float = 0.5) -> set[str]:
        """Beats the human used, by majority overlap.

        A beat counts as 'used' when more than `min_overlap` of it falls inside
        the human's selection. Editors cut mid-beat constantly, so an exact-match
        rule would find almost nothing.
        """
        from metrics import intersect

        out = set()
        for b in self.beats:
            ov = sum(e - s for s, e in intersect([b.interval], self.human))
            if b.frames and ov / b.frames > min_overlap:
                out.add(b.id)
        return out


def load_fixture(path: Path) -> Pair:
    data = json.loads(Path(path).read_text())
    beats = [
        Beat(
            id=b.get("id", f"b{i:03d}"),
            start=b["start"],
            end=b["end"],
            speaker=b.get("speaker", ""),
            text=b["text"],
            flags=b.get("flags", []),
        )
        for i, b in enumerate(data["beats"])
    ]
    return Pair(
        name=data.get("name", Path(path).stem),
        fps=data.get("fps", 25.0),
        beats=beats,
        human=[tuple(r) for r in data["human_cut"]],
        notes=data.get("notes", ""),
    )


def human_ranges_from_timeline(path: Path) -> list[Interval]:
    """Extract the human's used source ranges from an EDL, AAF, OTIO or XML.

    This is the function that makes real corpus entries cheap: point it at the
    editor's own export and it returns exactly what they used.

    EDL needs the rate passed in — it does not carry one. Spike A covers why.
    """
    import opentimelineio as otio

    suffix = Path(path).suffix.lower()
    kwargs = {}
    if suffix == ".edl":
        raise ValueError(
            "EDL carries no frame rate — call read_edl(path, fps) instead"
        )
    tl = otio.adapters.read_from_file(str(path), **kwargs)
    out: list[Interval] = []
    for track in tl.tracks:
        if track.kind != otio.schema.TrackKind.Video:
            continue
        for clip in track.find_clips():
            sr = clip.source_range
            start = round(sr.start_time.value)
            out.append((start, start + round(sr.duration.value)))
    return normalize(out)


def read_edl(path: Path, fps: float) -> list[Interval]:
    import opentimelineio as otio

    tl = otio.adapters.read_from_file(str(path), adapter_name="cmx_3600", rate=fps)
    out: list[Interval] = []
    for track in tl.tracks:
        for clip in track.find_clips():
            sr = clip.source_range
            start = round(sr.start_time.value)
            out.append((start, start + round(sr.duration.value)))
    return normalize(out)
