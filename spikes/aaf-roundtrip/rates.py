"""Frame rates and the cut plan.

Every value here is a rational. `23.976` is not a frame rate; 24000/1001 is.
See docs/architecture/02-media-and-interchange.md.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Rate:
    key: str
    num: int
    den: int
    drop_frame: bool

    @property
    def fps(self) -> float:
        return self.num / self.den

    @property
    def nominal(self) -> int:
        """Frames per second for timecode labelling: 29.97 -> 30."""
        return round(self.fps)

    @property
    def label(self) -> str:
        return f"{self.key}{' DF' if self.drop_frame else ''}"


RATES: dict[str, Rate] = {
    "23976": Rate("23976", 24000, 1001, False),
    "25": Rate("25", 25, 1, False),
    "2997ndf": Rate("2997ndf", 30000, 1001, False),
    "2997df": Rate("2997df", 30000, 1001, True),
}

# Source timecode start.
#
# 09:58:00:00 is chosen deliberately. A five-minute source from here crosses
# both kinds of drop-frame minute boundary:
#
#   09:58 -> 09:59   two frames dropped
#   09:59 -> 10:00   NO frames dropped (minute divisible by ten)
#   10:00 -> 10:01   two frames dropped
#
# Testing only one of those cases proves nothing. A source starting at
# 10:00:00:00 would need to run past 10:10:00 to reach the exception, which
# means a much larger test file for no extra coverage.
# Second :02 rather than :00 — at second :00 of a non-tenth minute the labels
# ;00 and ;01 do not exist in drop-frame, so 09:58:00;00 is not a timecode.
SOURCE_START_TC = (9, 58, 2, 0)
SOURCE_DURATION_S = 300


@dataclass(frozen=True)
class Cut:
    """One selection from the source.

    `offset_s` and `dur_s` are seconds from the start of the source media, kept
    in seconds here only because that is how a person reads a cut plan. They are
    converted to frames — exactly once, at build time — in timeline.py.
    """

    offset_s: float
    dur_s: float
    why: str


# The cut plan.
#
# This is not twenty arbitrary cuts. Each one is here to catch a specific class
# of bug, and the `why` is printed in the verification checklist so a failure
# points at a cause rather than a mystery.
CUT_PLAN: list[Cut] = [
    Cut(0.0, 4.0, "First frame of media — off-by-one at the head"),
    Cut(12.0, 3.0, "Ordinary cut, nothing special"),
    Cut(115.0, 10.0, "Spans 09:59 -> 10:00: the drop-frame EXCEPTION, no frames dropped"),
    Cut(30.0, 0.5, "Sub-second clip — minimum-duration and rounding"),
    Cut(175.0, 8.0, "Spans 10:00 -> 10:01: ordinary drop-frame boundary, two frames dropped"),
    Cut(60.0, 5.0, "Exactly on a whole second at the source start"),
    Cut(200.0, 6.0, "Out of source order — this must land AFTER the 10:00 cut in the record"),
    Cut(45.0, 2.0, "Backwards jump: earlier source, later in the record"),
    Cut(90.0, 4.0, "Ordinary"),
    Cut(94.0, 4.0, "Contiguous with the previous cut — must stay TWO clips, not merge into one"),
    Cut(150.0, 0.48, "12 frames at 25fps — very short clip survives the round trip"),
    Cut(235.0, 7.0, "Spans 10:01 -> 10:02: another ordinary drop-frame boundary"),
    Cut(20.0, 3.0, "Repeat of a region already used earlier in the timeline"),
    Cut(20.0, 3.0, "Same region again, twice in a row — duplicate source ranges"),
    Cut(260.0, 5.0, "Late in the media"),
    Cut(133.0, 3.5, "Non-integer second boundary, odd frame offset"),
    Cut(133.04, 3.5, "One frame later than the previous cut — single-frame precision"),
    Cut(70.0, 9.0, "Longer clip"),
    Cut(295.0, 4.5, "Runs to the very end of available media — tail boundary"),
    Cut(5.0, 2.0, "Final clip returns to the head of the source"),
]
