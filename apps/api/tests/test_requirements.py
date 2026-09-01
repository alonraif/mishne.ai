"""What a linked AAF is waiting for: the folding, the matching, and the states.

The DB half needs a migrated schema; the folding half is pure and runs anywhere.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

pytest.importorskip("sqlalchemy")

from mishne.db import requirements as reqs  # noqa: E402


@dataclass
class FakeClip:
    """Just enough of `aaf_ingest.SourceClip` to fold."""

    name: str
    target_url: str | None = None
    media_path: object | None = None
    embedded_mob_id: str | None = None
    mob_id: str = ""
    track_index: int = 0


# ─────────────────────────────────────────────────────────────────── the locator


@pytest.mark.parametrize(
    "url, expected",
    [
        ("file:///Volumes/Media/A001C002.mxf", "A001C002.mxf"),
        (r"C:\Avid MediaFiles\A001C002.mxf", "A001C002.mxf"),
        ("file:///Volumes/Media/Interview%20Take%202.mov", "Interview Take 2.mov"),
        ("/mnt/san/rushes/B002.mov", "B002.mov"),
        ("A001.mxf", "A001.mxf"),
        (None, ""),
        ("", ""),
    ],
)
def test_a_locator_is_reduced_to_the_file_the_customer_has(url, expected):
    # The absolute path inside an AAF describes a filesystem we will never see.
    # The basename is the only part that survives the trip.
    assert reqs.basename_of(url) == expected


def test_matching_ignores_case_because_the_editors_filesystem_does():
    assert reqs.match_key_for("A001.MXF") == reqs.match_key_for("a001.mxf")


# ────────────────────────────────────────────────────────────────── the folding


def test_one_requirement_per_file_not_per_clip():
    clips = [
        FakeClip("shot 1", "file:///san/A001.mxf"),
        FakeClip("shot 2", "file:///san/A001.mxf"),
        FakeClip("shot 3", "file:///san/B002.mxf"),
    ]
    wanted = reqs.from_clips(clips)
    assert [(r.basename, r.clip_count) for r in wanted] == [("A001.mxf", 2), ("B002.mxf", 1)]


def test_every_track_s_media_is_asked_for_not_just_the_first_track_s():
    """Four microphones on four tracks are four files to ask for.

    `from_clips` folds whatever the parse hands it, so this pins the contract
    from the other side: a sequence whose clips span several tracks must ask
    for all of their media. Asking for one of four and then transcribing one
    microphone is what happened before `parse` read past the first track.
    """
    clips = [
        FakeClip("Audio 1_L", "file:///D:/Export/AAF Media/mic0.wav", track_index=0),
        FakeClip("Audio 1_R", "file:///D:/Export/AAF Media/mic1.wav", track_index=1),
        FakeClip("Audio 2_L", "file:///D:/Export/AAF Media/mic2.wav", track_index=2),
        FakeClip("Audio 2_R", "file:///D:/Export/AAF Media/mic3.wav", track_index=3),
    ]

    wanted = reqs.from_clips(clips)

    assert sorted(r.basename for r in wanted) == [
        "mic0.wav", "mic1.wav", "mic2.wav", "mic3.wav"]


def test_the_file_that_unblocks_the_most_clips_is_asked_for_first():
    clips = [FakeClip("a", "file:///san/one.mxf")] + [
        FakeClip(f"b{i}", "file:///san/many.mxf") for i in range(5)
    ]
    assert reqs.from_clips(clips)[0].basename == "many.mxf"


def test_resolved_and_embedded_clips_are_not_asked_for():
    clips = [
        FakeClip("resolved", "file:///san/A001.mxf", media_path=Path("/tmp/A001.mxf")),
        FakeClip("embedded", "file:///san/B002.mxf", embedded_mob_id="urn:mob:1"),
        FakeClip("missing", "file:///san/C003.mxf"),
    ]
    assert [r.basename for r in reqs.from_clips(clips)] == ["C003.mxf"]


def test_a_clip_with_no_locator_asks_for_nothing_rather_than_inventing_a_name():
    # Sending a customer to look for a file that does not exist is worse than
    # telling them a clip is unresolvable.
    assert reqs.from_clips([FakeClip("nameless", None)]) == []


def test_an_embedded_sequence_wants_nothing():
    clips = [FakeClip(f"c{i}", "file:///san/x.mxf", embedded_mob_id="urn:mob:1")
             for i in range(20)]
    assert reqs.from_clips(clips) == []
