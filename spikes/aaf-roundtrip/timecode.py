"""SMPTE timecode conversion. One implementation, used everywhere.

This module exists because the spike found a bug in its own first draft:
`timeline.py` computed a start frame from a timecode label using non-drop
arithmetic, and `checklist.py` then formatted that frame count *as* drop-frame,
adding the skipped frames a second time. Source start `09:58:00;00` came out as
`09:58:35;28`.

That is the classic drop-frame failure and the reason to have exactly one
conversion pair rather than arithmetic scattered across modules.

## What drop-frame is

Drop-frame does not drop frames. It skips *labels*. At 29.97 the clock runs
slower than 30 fps, so a counter labelling 30 frames per second drifts ahead of
wall time by about 3.6 seconds an hour. Drop-frame corrects the labelling by
skipping two frame numbers at the start of every minute, except every tenth
minute. No picture is lost; only names are.

Consequences that matter here:

- `09:58:00;00` and `09:58:00:00` are *different absolute frames*.
- Timecode arithmetic on drop-frame strings is meaningless. Convert to an
  absolute frame count, do the arithmetic, convert back.
- **Some labels do not exist.** `;00` and `;01` are skipped at second `:00` of
  every minute not divisible by ten, so `09:58:00;00` is not a timecode. The
  spike's first draft used exactly that as its source start and got a silently
  wrong frame number back. `tc_to_frames` now refuses it rather than guessing —
  a timecode that cannot exist is a bug upstream, and returning *something*
  hides it.
"""


from __future__ import annotations

from rates import Rate


class InvalidTimecode(ValueError):
    """A timecode label that does not exist at this rate."""


def is_valid_tc(h: int, m: int, s: int, f: int, rate: Rate) -> bool:
    """False for drop-frame labels that are skipped and therefore never occur."""
    fps = rate.nominal
    if f >= fps or s > 59 or m > 59:
        return False
    if rate.drop_frame and fps in (30, 60):
        drop = 2 if fps == 30 else 4
        if s == 0 and m % 10 != 0 and f < drop:
            return False
    return True


def tc_to_frames(h: int, m: int, s: int, f: int, rate: Rate) -> int:
    """Absolute frame count for a timecode label.

    Raises InvalidTimecode for labels drop-frame skips.
    """
    if not is_valid_tc(h, m, s, f, rate):
        raise InvalidTimecode(
            f"{h:02d}:{m:02d}:{s:02d}"
            f"{';' if rate.drop_frame else ':'}{f:02d} "
            f"does not exist at {rate.label}"
        )
    fps = rate.nominal
    frames = ((h * 3600 + m * 60 + s) * fps) + f
    if rate.drop_frame and fps in (30, 60):
        drop = 2 if fps == 30 else 4
        total_minutes = h * 60 + m
        frames -= drop * (total_minutes - total_minutes // 10)
    return frames


def frames_to_tc(frames: int, rate: Rate) -> str:
    """Timecode label for an absolute frame count. Inverse of tc_to_frames."""
    fps = rate.nominal
    f = max(0, round(frames))

    if rate.drop_frame and fps in (30, 60):
        drop = 2 if fps == 30 else 4
        per_10min = fps * 60 * 10 - drop * 9
        per_min = fps * 60 - drop
        tens, rem = divmod(f, per_10min)
        f += drop * 9 * tens
        if rem > drop:
            f += drop * ((rem - drop) // per_min)

    ff = f % fps
    total_s = f // fps
    sep = ";" if rate.drop_frame else ":"
    return (f"{total_s // 3600 % 24:02d}:{total_s // 60 % 60:02d}:"
            f"{total_s % 60:02d}{sep}{ff:02d}")


# Hours worth walking. Not 0..n — the spike's own drop-frame bug lived at
# 09:58 and a self-test that only covered hour 0 passed happily while the
# checklist printed wrong numbers. Cover the hour the test media actually uses,
# the record-start hour, and the boundaries.
SELF_TEST_HOURS = (0, 1, 9, 10, 23)


def self_test(rate: Rate, hours: tuple[int, ...] = SELF_TEST_HOURS) -> list[str]:
    """Assert frames_to_tc is the exact inverse of tc_to_frames.

    Walks every frame of every named hour, skipping labels drop-frame does not
    have. Deliberately covers hours far from zero.
    """
    failures = []
    fps = rate.nominal
    for h in hours:
        for m in range(60):
            for s in range(60):
                for f in range(fps):
                    if not is_valid_tc(h, m, s, f, rate):
                        continue
                    n = tc_to_frames(h, m, s, f, rate)
                    back = frames_to_tc(n, rate)
                    want = (f"{h:02d}:{m:02d}:{s:02d}"
                            f"{';' if rate.drop_frame else ':'}{f:02d}")
                    if back != want:
                        failures.append(f"{want} -> {n} -> {back}")
                        if len(failures) > 5:
                            return failures
    return failures
