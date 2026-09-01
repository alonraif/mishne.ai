"""A real linked export, if there is one on this machine.

`test_aaf_ingest` builds its own AAF and proves the mechanism. This proves the
mechanism against what an editor actually hands over, which is a different
claim: locators written by Media Composer on a Windows volume that does not
exist here, media in a folder called `AAF Media`, and one 265 MB mono WAV per
microphone.

    MISHNE_SAMPLE_LINKED_AAF="../../samples/peppercreative_law_podcast_ep_39-gelem-aaf_2026-09-01_0859/Law_Podcast_EP_39 Gelem.aaf" \\
      .venv/bin/python -m pytest tests/test_linked_sample.py -q

Skipped without it, like `test_reference_run`. `samples/` is uncommitted and is
the irreplaceable part of this repository, so nothing here writes into it: the
flatten test stages its own working directory and the requirement test copies
the AAF away from its media rather than moving the media.
"""

from __future__ import annotations

import os
import shutil
import sys
import wave
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

SAMPLE = os.environ.get("MISHNE_SAMPLE_LINKED_AAF", "")

pytestmark = pytest.mark.skipif(
    not (SAMPLE and Path(SAMPLE).exists()),
    reason="no sample — set MISHNE_SAMPLE_LINKED_AAF to a real linked AAF",
)

pytest.importorskip("opentimelineio")

from mishne.pipeline.steps import aaf_ingest  # noqa: E402


@pytest.fixture(scope="module")
def source():
    return aaf_ingest.parse(Path(SAMPLE))


def test_the_media_beside_the_aaf_is_found_despite_the_locators(source):
    """The locators name a drive letter. The media is one level down, here."""
    assert not source.embedded
    assert source.clips, "the sequence parsed to no clips at all"
    unresolved = [c for c in source.clips if not c.resolved]
    assert unresolved == [], (
        f"{len(unresolved)} of {len(source.clips)} clips did not resolve; "
        f"first locator was {unresolved[0].target_url if unresolved else ''}"
    )


def test_every_sound_track_in_the_export_is_accounted_for(source):
    """One clip per microphone, each on its own track, all of them read."""
    assert len(source.tracks) >= 1
    assert len(source.clips) >= len(source.tracks)
    # The output is expressed against one track, whatever the mix is made from.
    assert all(c.track_index == 0 for c in source.primary_clips)


def test_the_platform_would_ask_for_every_referenced_file(tmp_path, source):
    """The AAF alone, as the browser uploads it: what does the customer see?"""
    pytest.importorskip("sqlalchemy")
    from mishne.db import requirements as reqs

    alone = tmp_path / "as-uploaded"
    alone.mkdir()
    shutil.copy2(SAMPLE, alone / Path(SAMPLE).name)

    orphan = aaf_ingest.parse(alone / Path(SAMPLE).name)

    wanted = reqs.from_clips(orphan.clips)
    assert len(wanted) == len(source.clips), (
        "the number of files asked for must match the number of clips that "
        "reference external media"
    )
    # The basenames are the ones sitting in `AAF Media/`, so dropping that
    # folder on the requirements panel satisfies every row.
    on_disk = {p.name.lower() for p in (Path(SAMPLE).parent / "AAF Media").iterdir()}
    assert all(r.basename.lower() in on_disk for r in wanted)


@pytest.mark.slow
def test_the_flattened_mix_is_the_sequence_s_length(tmp_path, source):
    """Slow: this renders every microphone at full length before mixing."""
    flat = aaf_ingest.flatten_audio(source, tmp_path / "work")

    with wave.open(str(flat), "rb") as w:
        assert w.getnchannels() == 1
        assert w.getframerate() == aaf_ingest.SAMPLE_RATE
        seconds = w.getnframes() / w.getframerate()
    assert abs(seconds - source.duration_s) < 0.5

    mics = aaf_ingest.track_renders(source, tmp_path / "work")
    assert len(mics) == (len(source.tracks) if len(source.tracks) > 1 else 0)
