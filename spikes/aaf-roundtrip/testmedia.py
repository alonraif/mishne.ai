"""Generate test source media.

The point of this media is that a wrong cut is *obvious*. Every frame carries
its own timecode burned into the picture, and the audio carries a marker on
every second boundary. Open the AAF in Media Composer, look at the first frame
of clip three, and either it reads the timecode the checklist says it should or
it does not. No scrubbing, no guessing.

Without burned-in reference, "verify the timecode is right" is an instruction
nobody can actually follow.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from rates import SOURCE_DURATION_S, SOURCE_START_TC, Rate

FONT_CANDIDATES = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    "/Library/Fonts/Arial Bold.ttf",
]

CODECS = {
    # ProRes Proxy: what an NLE is happiest linking to, still small.
    "prores": ["-c:v", "prores_ks", "-profile:v", "0", "-pix_fmt", "yuv422p10le"],
    # Fast and tiny, for a quick structural pass.
    "h264": ["-c:v", "libx264", "-preset", "veryfast", "-crf", "26",
             "-pix_fmt", "yuv420p"],
    "dnxhd": ["-c:v", "dnxhd", "-profile:v", "dnxhr_lb", "-pix_fmt", "yuv422p"],
}


def find_font() -> str:
    for f in FONT_CANDIDATES:
        if Path(f).is_file():
            return f
    raise RuntimeError(
        "No usable font found for burned-in timecode. Install DejaVu or "
        "Liberation fonts, or add a path to FONT_CANDIDATES."
    )


def tc_plain(rate: Rate) -> str:
    """Timecode as ffmpeg's -timecode flag wants it: hh:mm:ss[:;]ff."""
    h, m, s, f = SOURCE_START_TC
    sep = ";" if rate.drop_frame else ":"
    return f"{h:02d}:{m:02d}:{s:02d}{sep}{f:02d}"


def tc_escaped(rate: Rate) -> str:
    """Same value, escaped for a drawtext filter argument.

    Every separator needs a backslash, the drop-frame semicolon included.
    Missing one produces `Unable to parse timecode, syntax: hh:mm:ss[:;.]ff`,
    because ffmpeg has already split the filter argument on the bare colon.
    """
    return tc_plain(rate).replace(":", "\\:").replace(";", "\\;")


def _is_complete(path: Path, expected_s: float, tolerance_s: float = 1.0) -> bool:
    """True if `path` probes as a readable file of about the expected length."""
    try:
        proc = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=nw=1:nk=1", str(path)],
            capture_output=True, text=True, timeout=30,
        )
        if proc.returncode != 0:
            return False
        return abs(float(proc.stdout.strip()) - expected_s) <= tolerance_s
    except (ValueError, subprocess.SubprocessError):
        return False


def generate(rate: Rate, out_dir: Path, codec: str = "prores",
             duration_s: int = SOURCE_DURATION_S, width: int = 640,
             height: int = 360) -> Path:
    """Render one test source for a given rate. Returns the file path."""
    if shutil.which("ffmpeg") is None:
        raise RuntimeError("ffmpeg not found on PATH")

    out_dir.mkdir(parents=True, exist_ok=True)
    # The start timecode is in the filename on purpose. It is burned into every
    # frame, so changing SOURCE_START_TC invalidates existing media — and a
    # duration check cannot see that. Encoding it here means stale media is a
    # cache miss rather than a wrong answer.
    h, m, sec, fr = SOURCE_START_TC
    stamp = f"{h:02d}{m:02d}{sec:02d}{fr:02d}"
    out = out_dir / f"MISHNE_TESTSRC_{rate.key}_{stamp}.mov"

    # Reuse existing media only if it is actually complete. An interrupted
    # render leaves a truncated file that still "exists", and silently reusing
    # it poisons every downstream check with timings that look plausible and
    # are wrong.
    if out.exists() and _is_complete(out, duration_s):
        return out

    font = find_font()
    fps = f"{rate.num}/{rate.den}"

    # Burned-in running timecode, plus the rate label so a mixed-up file is
    # obvious at a glance.
    vf = (
        f"drawtext=fontfile={font}:timecode='{tc_escaped(rate)}':"
        f"rate={fps}:fontsize={height // 7}:fontcolor=white:"
        f"box=1:boxcolor=black@0.75:boxborderw=8:x=(w-tw)/2:y=h*0.36,"
        f"drawtext=fontfile={font}:text='{rate.label}':"
        f"fontsize={height // 14}:fontcolor=yellow:"
        f"box=1:boxcolor=black@0.6:boxborderw=6:x=(w-tw)/2:y=h*0.62"
    )

    # ch1: a 1 kHz blip on every second boundary — the visible waveform spike
    #      an editor uses to confirm a cut landed where it should.
    # ch2: a 440 Hz blip every ten seconds, so channel swaps are visible too.
    a1 = "sine=frequency=1000:sample_rate=48000:duration=%d,volume='if(lt(mod(t,1),0.04),1,0)':eval=frame" % duration_s
    a2 = "sine=frequency=440:sample_rate=48000:duration=%d,volume='if(lt(mod(t,10),0.15),1,0)':eval=frame" % duration_s

    cmd = [
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-f", "lavfi", "-i", f"testsrc2=size={width}x{height}:rate={fps}:duration={duration_s}",
        "-f", "lavfi", "-i", a1,
        "-f", "lavfi", "-i", a2,
        "-filter_complex", f"[0:v]{vf}[v];[1:a][2:a]amerge=inputs=2[a]",
        "-map", "[v]", "-map", "[a]",
        *CODECS[codec],
        "-c:a", "pcm_s16le", "-ac", "2",
        "-r", fps,
        "-timecode", tc_plain(rate),
        str(out),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        # ffmpeg's own message is the only useful diagnostic here; a bare
        # CalledProcessError tells you nothing about which filter broke.
        raise RuntimeError(
            f"ffmpeg failed rendering {out.name}:\n"
            + (proc.stderr.strip() or "(no stderr)")
        )
    return out
