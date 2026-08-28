"""SMPTE timecode. One implementation, used everywhere.

Ported from spikes/aaf-roundtrip/timecode.py, where it is exercised by a
self-test that walks every frame of several hours at four rates.

Drop-frame does not drop frames, it skips *labels*: two frame numbers at the
start of every minute except every tenth. Consequences that bite:

- `09:58:00;00` and `09:58:00:00` are different absolute frames.
- Some labels do not exist at all. `tc_to_frames` raises rather than guessing —
  a timecode that cannot exist is a bug upstream, and returning something hides
  it.
- Never do arithmetic on timecode strings. Convert to frames, compute, convert
  back.
"""

from __future__ import annotations

from dataclasses import dataclass


class InvalidTimecode(ValueError):
    """A timecode label that does not exist at this rate."""


@dataclass(frozen=True)
class Rate:
    num: int
    den: int
    drop_frame: bool = False

    @property
    def fps(self) -> float:
        return self.num / self.den

    @property
    def nominal(self) -> int:
        return round(self.fps)

    def __str__(self) -> str:
        return f"{self.num}/{self.den}{' DF' if self.drop_frame else ''}"


def is_valid_tc(h: int, m: int, s: int, f: int, rate: Rate) -> bool:
    fps = rate.nominal
    if f >= fps or s > 59 or m > 59:
        return False
    if rate.drop_frame and fps in (30, 60):
        drop = 2 if fps == 30 else 4
        if s == 0 and m % 10 != 0 and f < drop:
            return False
    return True


def tc_to_frames(h: int, m: int, s: int, f: int, rate: Rate) -> int:
    if not is_valid_tc(h, m, s, f, rate):
        raise InvalidTimecode(
            f"{h:02d}:{m:02d}:{s:02d}{';' if rate.drop_frame else ':'}{f:02d} "
            f"does not exist at {rate}"
        )
    fps = rate.nominal
    frames = ((h * 3600 + m * 60 + s) * fps) + f
    if rate.drop_frame and fps in (30, 60):
        drop = 2 if fps == 30 else 4
        total_minutes = h * 60 + m
        frames -= drop * (total_minutes - total_minutes // 10)
    return frames


def frames_to_tc(frames: int, rate: Rate) -> str:
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


def parse_tc(text: str, rate: Rate) -> int:
    """Parse 'hh:mm:ss:ff' or 'hh:mm:ss;ff' to an absolute frame count."""
    parts = text.replace(";", ":").replace(".", ":").split(":")
    if len(parts) != 4:
        raise InvalidTimecode(f"cannot parse timecode {text!r}")
    h, m, s, f = (int(p) for p in parts)
    return tc_to_frames(h, m, s, f, rate)


def ms_to_frames(ms: float, rate: Rate) -> int:
    """Wall-clock milliseconds to a frame count at the true (not nominal) rate."""
    return round(ms / 1000.0 * rate.fps)
