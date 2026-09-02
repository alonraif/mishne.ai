"""The preview rendition — 720p H.264, or AAC where there is no picture.

## What this is for

The cut editor asks a person to choose lines. Read as text, a transcript says
what was said and nothing whatever about whether the take was usable: you cannot
hear the hesitation, and you cannot see the moment the subject looks off camera.
So every asset gets one small, seekable rendition that a browser can play, and
the editor welds it to the text. See ADR-0020.

## The rule this module exists to keep

**Position in the proxy equals position in the source, exactly.** The whole
feature is a mapping between a media element's `currentTime` and a beat's source
timecode, and that mapping is `(frames - start_tc) / fps` with no correction
term anywhere. Two consequences, both non-negotiable:

* **The frame rate is never touched.** No `-r`, no `fps` filter. Resampling
  23.976 to 24 makes the transcript drift a frame every 42 seconds — an hour in
  and the highlight is a second and a half ahead of the voice, which reads as
  "the player is broken" long before anyone suspects the encoder.
* **The result is measured, not assumed.** `verify` re-probes the finished file
  and refuses it if its duration has moved by more than a frame. Stage 11
  re-parses every artifact it writes and diffs it against the timeline; this is
  the same instinct applied to the same class of bug, and it is what turns a
  silent desync into a failed transcode.

## Why the numbers are what they are

`-crf 26` with `-maxrate 1M` is quality-targeted with a ceiling rather than a
flat bitrate: an interview locked off on a tripod spends far less than the cap,
a handheld exterior spends all of it, and neither ends up with a proxy that is
bigger than it needs to be or too coarse to judge a performance from. A
three-hour rush lands near 1.4 GB.

`-movflags +faststart` moves the index to the front of the file. Without it the
browser downloads the entire proxy before it can show a frame, which for three
hours of footage is indistinguishable from the feature not working.

`-g` at two seconds is the seek granularity. Every click on a beat is a seek, so
this is the number that decides whether the editor feels immediate.

Audio is mixed to mono at 64 kbps. This is a preview of speech for an editor
deciding whether a line is usable, not a mix anyone will deliver.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path

from ...logging import get_logger
from .prepare import MediaInfo

log = get_logger(__name__)

#: The tallest a preview gets. Wide enough to read a face, small enough that the
#: first seek over a thin connection is not a wait.
MAX_HEIGHT = 720

#: The ceiling, not the target — see the module docstring on `-crf`.
VIDEO_MAX_BITRATE = "1M"
VIDEO_BUFSIZE = "2M"
VIDEO_CRF = "26"

#: x264's speed/compression trade-off.
#:
#: **A faster preset costs storage, not quality.** `-crf` targets a quality
#: level, so x264 hits the same picture either way and a faster search simply
#: spends more bits getting there. Measured on a real 25-minute 640x360
#: interview at CRF 26: veryfast 44 MB in 65 s, superfast 64 MB in 33 s,
#: ultrafast 145 MB in 22 s, all within a decibel of each other on PSNR. Only
#: when a source is dense enough to pin `-maxrate` does the ceiling take over
#: and the trade become quality instead.
#:
#: superfast halves the encode for about 45% more storage, which is the right
#: way round for a file that is written once and streamed often. Roughly six
#: minutes for a three-hour rush, off the critical path (ADR-0020).
VIDEO_PRESET = "superfast"

AUDIO_BITRATE = "64k"
AUDIO_SAMPLE_RATE = "48000"

#: How far the finished proxy may differ from the source before it is refused,
#: in frames. One: anything that has actually resampled the rate drifts far
#: further than this within a minute, and a container rounding the last frame
#: differently is not a reason to fail somebody's upload.
DURATION_TOLERANCE_FRAMES = 1

VIDEO_NAME = "proxy.mp4"
AUDIO_NAME = "proxy.m4a"


@dataclass
class Proxy:
    """What was built, and enough about it to record a row."""

    path: Path
    #: "video" or "audio" — matches `db.vocab.PROXY_KINDS`.
    kind: str
    bytes: int

    @property
    def name(self) -> str:
        return self.path.name


class ProxyError(RuntimeError):
    """A transcode that did not produce something playable.

    Carries no ffmpeg stderr and no filename: this is raised across a boundary
    that logs, and ffmpeg puts the customer's path in almost every message it
    writes (docs/architecture/04-security.md).
    """


def build(
    info: MediaInfo,
    out_dir: Path,
    *,
    force: bool = False,
    threads: int = 0,
) -> Proxy:
    """One playable rendition of `info.path`. Idempotent.

    `info` is stage 0's probe of the same file, which is where `has_video`,
    `height` and `duration_frames` come from — re-probing here would be a second
    opinion about the time base, and there is only ever one.

    For a sequence, `info.path` is the flattened sound mix rather than the
    upload, because that render *is* the programme (ADR-0019). There is no
    picture to preview and this produces the audio rendition accordingly.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    video = bool(info.has_video and info.height > 0)
    out = out_dir / (VIDEO_NAME if video else AUDIO_NAME)

    if out.exists() and not force:
        # A warm asset directory, or a retried step. Rebuilding costs minutes
        # and produces the same bytes.
        return Proxy(path=out, kind="video" if video else "audio",
                     bytes=out.stat().st_size)

    cmd = (_video_command(info, out) if video else _audio_command(info, out))
    if threads > 0:
        # Leave the machine something. Irrelevant on a box whose whole job is
        # this, which is where previews belong (ADR-0021), and the difference
        # between a usable and an unusable laptop when it is not.
        cmd = [cmd[0], "-threads", str(threads), *cmd[1:]]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        # The exit status, not the message. See ProxyError.
        raise ProxyError(f"ffmpeg exited {proc.returncode} building the preview")
    if not out.exists() or out.stat().st_size == 0:
        raise ProxyError("ffmpeg reported success and wrote nothing")

    verify(info, out)
    proxy = Proxy(path=out, kind="video" if video else "audio",
                  bytes=out.stat().st_size)
    log.info("proxy.built", kind=proxy.kind, bytes=proxy.bytes,
             source_frames=info.duration_frames)
    return proxy


def _video_command(info: MediaInfo, out: Path) -> list[str]:
    fps = max(1, round(info.rate.fps))
    cmd = [
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-i", str(info.path),
        # The first video stream and whatever audio there is. `0:a?` rather than
        # `0:a` because a picture-only source is not an error here — it is a
        # perfectly good preview with nothing to hear.
        "-map", "0:v:0", "-map", "0:a?",
    ]
    # Only ever downscale. Blowing a 480p archive clip up to 720 costs bitrate
    # and adds nothing a person can see.
    if info.height > MAX_HEIGHT:
        # -2 rather than -1: H.264 needs even dimensions, and an odd width from
        # an unusual aspect ratio fails the encode outright.
        cmd += ["-vf", f"scale=-2:{MAX_HEIGHT}"]
    cmd += [
        "-c:v", "libx264", "-preset", VIDEO_PRESET, "-profile:v", "high",
        "-pix_fmt", "yuv420p",
        "-crf", VIDEO_CRF, "-maxrate", VIDEO_MAX_BITRATE, "-bufsize", VIDEO_BUFSIZE,
        # Two seconds between keyframes, and no extra ones at scene changes: a
        # predictable seek grid matters more here than compression efficiency.
        "-g", str(fps * 2), "-keyint_min", str(fps), "-sc_threshold", "0",
        "-c:a", "aac", "-b:a", AUDIO_BITRATE, "-ac", "1", "-ar", AUDIO_SAMPLE_RATE,
        "-movflags", "+faststart",
        str(out),
    ]
    return cmd


def _audio_command(info: MediaInfo, out: Path) -> list[str]:
    return [
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-i", str(info.path),
        "-vn",
        "-c:a", "aac", "-b:a", AUDIO_BITRATE, "-ac", "1", "-ar", AUDIO_SAMPLE_RATE,
        "-movflags", "+faststart",
        str(out),
    ]


def verify(info: MediaInfo, out: Path) -> float:
    """Refuse a proxy whose clock does not match the source's. Returns seconds.

    The failure this catches is a variable-rate source, or any future edit to
    the command that lets ffmpeg pick a frame rate of its own. Both produce a
    file that plays perfectly and sits progressively further from the transcript
    the longer it runs, and neither shows up in the exit status.
    """
    actual = duration_seconds(out)
    expected = info.duration_s
    tolerance = DURATION_TOLERANCE_FRAMES / info.rate.fps
    if abs(actual - expected) > tolerance:
        raise ProxyError(
            "preview duration does not match the source: "
            f"{actual:.3f}s against {expected:.3f}s "
            f"(tolerance {tolerance:.3f}s). The frame rate was not preserved."
        )
    return actual


def duration_seconds(path: Path) -> float:
    proc = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
        capture_output=True, text=True,
    )
    if proc.returncode != 0:
        raise ProxyError(f"ffprobe exited {proc.returncode} reading the preview")
    try:
        return float(proc.stdout.strip())
    except ValueError as exc:
        raise ProxyError("ffprobe reported no duration for the preview") from exc
