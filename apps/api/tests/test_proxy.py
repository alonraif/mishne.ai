"""The preview rendition — the properties a synced player depends on.

Every assertion here is a bug that reads as "the player is broken" rather than
as anything about the encoder. See ADR-0020 and `pipeline/steps/proxy.py`.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from mishne.pipeline.steps import prepare, proxy
from mishne.timecode import Rate

pytestmark = pytest.mark.skipif(
    shutil.which("ffmpeg") is None, reason="ffmpeg not installed")

NTSC = Rate(24000, 1001)


def _streams(path: Path) -> list[dict]:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_streams", "-of", "json", str(path)],
        capture_output=True, text=True, check=True,
    )
    return json.loads(out.stdout)["streams"]


def _video(path: Path) -> dict:
    return next(s for s in _streams(path) if s["codec_type"] == "video")


def _make(tmp_path: Path, name: str, *, size: str, rate: str,
          seconds: int = 4, audio: bool = True) -> Path:
    out = tmp_path / name
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
           "-f", "lavfi", "-i", f"testsrc=size={size}:rate={rate}:duration={seconds}"]
    if audio:
        cmd += ["-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}"]
    cmd += ["-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p"]
    if audio:
        cmd += ["-c:a", "aac"]
    cmd += [str(out)]
    subprocess.run(cmd, check=True, capture_output=True)
    return out


def test_the_frame_rate_survives_the_transcode(tmp_path):
    """The one that desyncs the transcript, silently and progressively.

    23.976 resampled to 24 drifts a frame every 42 seconds. An hour into a
    three-hour interview the highlight is a second and a half ahead of the
    voice, and nothing about that failure points at the encoder.
    """
    src = _make(tmp_path, "src.mp4", size="1920x1080", rate="24000/1001")
    info = prepare.probe(src)
    built = proxy.build(info, tmp_path / "out")

    assert _video(built.path)["r_frame_rate"] == "24000/1001"
    # And the clock agrees, which is what `verify` is asserting on every build.
    assert proxy.duration_seconds(built.path) == pytest.approx(
        info.duration_s, abs=1 / NTSC.fps
    )


def test_a_tall_source_is_capped_and_a_short_one_is_left_alone(tmp_path):
    tall = prepare.probe(_make(tmp_path, "tall.mp4", size="1920x1080", rate="25"))
    short = prepare.probe(_make(tmp_path, "short.mp4", size="640x480", rate="25"))

    capped = _video(proxy.build(tall, tmp_path / "a").path)
    assert capped["height"] == proxy.MAX_HEIGHT
    # -2, not -1: an odd width fails the encode outright.
    assert capped["width"] % 2 == 0

    # Upscaling a 480p archive clip costs bitrate and shows nobody anything.
    assert _video(proxy.build(short, tmp_path / "b").path)["height"] == 480


def test_the_index_is_at_the_front_or_nothing_plays_until_it_is_downloaded(tmp_path):
    """`-movflags +faststart`. Without it a three-hour preview shows no frame
    until the whole file has arrived, which is the feature not working."""
    info = prepare.probe(_make(tmp_path, "src.mp4", size="1280x720", rate="25"))
    built = proxy.build(info, tmp_path / "out")
    head = built.path.read_bytes()[:200_000]
    assert 0 <= head.find(b"moov") < head.find(b"mdat")


def test_a_source_with_no_picture_gets_a_sound_preview(tmp_path):
    """The AAF case: `flatten_audio`'s mix is the whole programme (ADR-0019)."""
    wav = tmp_path / "flat.wav"
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
         "-f", "lavfi", "-i", "sine=frequency=300:duration=4",
         "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(wav)],
        check=True, capture_output=True,
    )
    # A sequence carries no rate of its own in the rendered file; stage 0 passes
    # the AAF's.
    info = prepare.probe(wav, assume_rate=Rate(25, 1))
    built = proxy.build(info, tmp_path / "out")

    assert built.kind == "audio"
    assert built.path.suffix == ".m4a"
    assert [s["codec_type"] for s in _streams(built.path)] == ["audio"]


def test_a_preview_whose_clock_moved_is_refused(tmp_path):
    """`verify` is the whole defence against a silent desync.

    Nothing in ffmpeg's exit status distinguishes a correct transcode from one
    that quietly changed the frame rate, so the result is measured instead.
    """
    src = _make(tmp_path, "src.mp4", size="1280x720", rate="24000/1001", seconds=8)
    info = prepare.probe(src)
    wrong = tmp_path / "wrong.mp4"
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(src),
         "-t", "5", "-c:v", "libx264", "-preset", "ultrafast", "-an", str(wrong)],
        check=True, capture_output=True,
    )
    with pytest.raises(proxy.ProxyError):
        proxy.verify(info, wrong)


def test_building_twice_does_not_re_encode(tmp_path):
    """A warm asset directory and a retried step both land here. Rebuilding is
    minutes of ffmpeg to produce the same bytes."""
    info = prepare.probe(_make(tmp_path, "src.mp4", size="1280x720", rate="25"))
    first = proxy.build(info, tmp_path / "out")
    stamp = first.path.stat().st_mtime_ns
    again = proxy.build(info, tmp_path / "out")
    assert again.path.stat().st_mtime_ns == stamp
    assert again.bytes == first.bytes
