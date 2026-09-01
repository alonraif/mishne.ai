"""Ingest a real AAF — built here, parsed here, flattened here.

Every other AAF test in this suite works around the file. `test_probe` and
`test_requirements` monkeypatch `aaf_ingest.parse` out, `test_relink` builds
`SourceClip`s by hand, and the only test that reads a real sequence is
`test_reference_run`, which skips unless somebody exports two environment
variables. So the module that turns 300 KB of OLE structured storage into a
source map had no test that ran.

The fixture is what a linked export looks like: a composition with one sound
track per microphone, no embedded essence, and locators holding a Windows
absolute path into a directory that does not exist on this machine — with the
media one level down in `AAF Media/`, which is where Media Composer puts it.
"""

from __future__ import annotations

import math
import struct
import sys
import wave
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

pytest.importorskip("aaf2")
pytest.importorskip("opentimelineio")
import aaf2  # noqa: E402
import numpy as np  # noqa: E402

from mishne.pipeline.steps import aaf_ingest  # noqa: E402

FPS = 25
SOURCE_RATE = 48000
#: What the AAF claims, and it is a lie everywhere but the machine that exported
#: it. Percent-encoded drive letter, backslash-free, `AAF Media` with a space.
FAKE_PREFIX = "file:///D%3a/Somewhere/Export/ForMishne/AAF%20Media/"


def _tone(path: Path, seconds: float, hz: int, amp: float = 0.4) -> None:
    n = int(seconds * SOURCE_RATE)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SOURCE_RATE)
        w.writeframes(b"".join(
            struct.pack("<h", int(amp * 32767 * math.sin(2 * math.pi * hz * i / SOURCE_RATE)))
            for i in range(n)
        ))


def _linked_aaf(root: Path, hzs: tuple[int, ...], seconds: float = 2.0) -> Path:
    """One sound track per tone, media in `AAF Media/`, nothing embedded."""
    root.mkdir(parents=True, exist_ok=True)
    media = root / "AAF Media"
    media.mkdir(exist_ok=True)

    wavs = []
    for i, hz in enumerate(hzs):
        wav = media / f"mic{i}.wav"
        _tone(wav, seconds, hz)
        wavs.append(wav)

    aaf = root / "Mics.aaf"
    with aaf2.open(str(aaf), "w") as f:
        comp = f.create.CompositionMob("Mics")
        f.content.mobs.append(comp)
        for i, wav in enumerate(wavs):
            master = f.create.MasterMob(f"mic{i}")
            f.content.mobs.append(master)
            # `offline` is the whole point: a descriptor and no essence.
            master.import_audio_essence(str(wav), SOURCE_RATE, offline=True)
            for mob in list(f.content.mobs):
                if mob.name == f"mic{i}.PHYS":
                    locator = f.create.NetworkLocator()
                    locator["URLString"].value = FAKE_PREFIX + wav.name
                    mob.descriptor["Locator"].append(locator)
            slot = comp.create_sound_slot(edit_rate=FPS)
            slot.name = f"Audio {i + 1}"
            slot.segment = master.create_source_clip(
                slot_id=1, length=int(seconds * FPS))
    return aaf


def _samples(wav: Path) -> np.ndarray:
    with wave.open(str(wav), "rb") as w:
        assert w.getnchannels() == 1
        assert w.getframerate() == aaf_ingest.SAMPLE_RATE
        return np.frombuffer(w.readframes(w.getnframes()), dtype="<i2").astype(float)


def _energy_at(wav: Path, hz: int) -> float:
    """Normalised magnitude at one frequency. A mixed-in tone is not subtle."""
    data = _samples(wav)
    spectrum = np.abs(np.fft.rfft(data * np.hanning(len(data))))
    freqs = np.fft.rfftfreq(len(data), 1 / aaf_ingest.SAMPLE_RATE)
    bin_at = int(np.argmin(np.abs(freqs - hz)))
    return float(spectrum[bin_at] / (spectrum.max() or 1.0))


# ── resolution ──────────────────────────────────────────────────────────────


def test_the_search_path_leads_with_the_aaf_and_includes_the_media_folder(tmp_path):
    """`AAF Media/` is not exotic; it is what the export produces."""
    aaf = _linked_aaf(tmp_path / "export", (440,))
    dirs = aaf_ingest.search_dirs_for(aaf)

    # The AAF's own directory first: that is where a worker materialises the
    # companions, and ADR-0014 rests on finding them there.
    assert dirs[0] == aaf.parent
    assert aaf.parent / "AAF Media" in dirs


def test_a_windows_locator_resolves_to_media_one_level_down(tmp_path):
    aaf = _linked_aaf(tmp_path / "export", (440,))
    url = FAKE_PREFIX + "mic0.wav"

    found = aaf_ingest._url_to_path(url, aaf_ingest.search_dirs_for(aaf))

    assert found is not None
    assert found == aaf.parent / "AAF Media" / "mic0.wav"
    # The path the AAF actually names does not exist on this machine.
    assert not Path("/D:/Somewhere/Export/ForMishne/AAF Media/mic0.wav").exists()


def test_a_locator_with_nothing_behind_it_resolves_to_nothing(tmp_path):
    aaf = _linked_aaf(tmp_path / "export", (440,))

    assert aaf_ingest._url_to_path(FAKE_PREFIX + "absent.wav",
                                   aaf_ingest.search_dirs_for(aaf)) is None
    assert aaf_ingest._url_to_path(None, [tmp_path]) is None


def test_media_kept_somewhere_else_entirely_is_found_when_named(tmp_path):
    """The `--media-dir` case: the folder was moved after the export."""
    aaf = _linked_aaf(tmp_path / "export", (440,))
    elsewhere = tmp_path / "moved"
    (aaf.parent / "AAF Media").rename(elsewhere)

    assert aaf_ingest._url_to_path(FAKE_PREFIX + "mic0.wav",
                                   aaf_ingest.search_dirs_for(aaf)) is None
    found = aaf_ingest._url_to_path(
        FAKE_PREFIX + "mic0.wav", aaf_ingest.search_dirs_for(aaf, [elsewhere]))
    assert found == elsewhere / "mic0.wav"


# ── the parse ───────────────────────────────────────────────────────────────


def test_every_sound_track_is_read_not_just_the_first(tmp_path):
    """The bug this test exists for: four mics on four tracks read as one.

    A podcast AAF keeps each microphone on its own track. Reading the first one
    asks the customer to upload a quarter of the media and then transcribes a
    quarter of the room.
    """
    aaf = _linked_aaf(tmp_path / "export", (300, 900, 1500, 2100))

    source = aaf_ingest.parse(aaf)

    assert source.tracks == [0, 1, 2, 3]
    assert len(source.clips) == 4
    assert source.missing == []
    assert [c.track_index for c in source.clips] == [0, 1, 2, 3]
    assert [c.track_name for c in source.clips] == [
        "Audio 1", "Audio 2", "Audio 3", "Audio 4"]


def test_parallel_tracks_do_not_lengthen_the_sequence(tmp_path):
    """Tracks run alongside each other; the duration is the longest, not the sum."""
    aaf = _linked_aaf(tmp_path / "export", (300, 900), seconds=2.0)

    source = aaf_ingest.parse(aaf)

    assert source.duration_frames == 2 * FPS
    assert all(c.tl_in == 0 for c in source.clips)


def test_the_cut_is_expressed_against_one_track(tmp_path):
    """`clips` is what to transcribe; `primary_clips` is what the output says."""
    aaf = _linked_aaf(tmp_path / "export", (300, 900))

    source = aaf_ingest.parse(aaf)

    assert len(source.primary_clips) == 1
    assert source.primary_clips[0].track_index == source.primary_track == 0
    # A timeline range maps back to the primary track alone, so a selection
    # never turns into four stacked copies of itself.
    mapped = aaf_ingest.map_to_source(source, 0, 10)
    assert len(mapped) == 1
    assert mapped[0][0].track_index == 0


def test_an_aaf_with_no_media_asks_for_every_track_s_file(tmp_path):
    """What the platform shows the customer: one row per referenced file."""
    pytest.importorskip("sqlalchemy")
    from mishne.db import requirements as reqs

    aaf = _linked_aaf(tmp_path / "export", (300, 900, 1500, 2100))
    alone = tmp_path / "alone"
    alone.mkdir()
    aaf = Path(str(aaf.replace(alone / aaf.name)))  # the AAF, without its media

    source = aaf_ingest.parse(aaf)

    assert len(source.missing) == 4
    wanted = sorted(r.basename for r in reqs.from_clips(source.clips))
    assert wanted == ["mic0.wav", "mic1.wav", "mic2.wav", "mic3.wav"]


# ── the flatten ─────────────────────────────────────────────────────────────


def test_one_track_is_not_mixed(tmp_path):
    """The single-track path is the one that always ran: no mix, same bytes."""
    aaf = _linked_aaf(tmp_path / "export", (440,))
    out_dir = tmp_path / "work"

    flat = aaf_ingest.flatten_audio(aaf_ingest.parse(aaf), out_dir)

    assert flat.exists()
    # No per-track intermediate was written, which is how we know the mix
    # filter was never reached.
    assert list(out_dir.glob("_track_*.wav")) == []
    assert _energy_at(flat, 440) > 0.5


def test_every_microphone_is_audible_in_the_mix(tmp_path):
    """Four tones in, four tones out. This is what makes the transcript whole."""
    hzs = (300, 900, 1500, 2100)
    aaf = _linked_aaf(tmp_path / "export", hzs)
    out_dir = tmp_path / "work"

    flat = aaf_ingest.flatten_audio(aaf_ingest.parse(aaf), out_dir)

    assert len(list(out_dir.glob("_track_*.wav"))) == 4
    for hz in hzs:
        assert _energy_at(flat, hz) > 0.3, f"{hz} Hz did not survive the mix"


def test_the_mix_does_not_clip(tmp_path):
    """Summing four mics must not drive the result into the ceiling.

    `normalize=0` sums rather than averages, so the trim is what keeps a loud
    passage off the rail. A clipped mix transcribes worse than a quiet one.
    """
    aaf = _linked_aaf(tmp_path / "export", (300, 900, 1500, 2100))

    flat = aaf_ingest.flatten_audio(aaf_ingest.parse(aaf), tmp_path / "work")

    peak = float(np.max(np.abs(_samples(flat))))
    assert peak < 32767, "the mix is at full scale — it is clipping"
    assert peak > 3000, "the mix is far too quiet to transcribe"


def test_the_flattened_length_is_the_sequence_length(tmp_path):
    """Position in the file equals position on the timeline. Stage 10 needs it."""
    aaf = _linked_aaf(tmp_path / "export", (300, 900), seconds=2.0)
    source = aaf_ingest.parse(aaf)

    flat = aaf_ingest.flatten_audio(source, tmp_path / "work")

    seconds = len(_samples(flat)) / aaf_ingest.SAMPLE_RATE
    assert abs(seconds - source.duration_s) < 0.05


def test_unresolved_media_becomes_silence_of_the_right_length(tmp_path):
    """A sequence we cannot resolve still has to keep its shape."""
    aaf = _linked_aaf(tmp_path / "export", (300, 900), seconds=2.0)
    alone = tmp_path / "alone"
    alone.mkdir()
    aaf = Path(str(aaf.replace(alone / aaf.name)))
    source = aaf_ingest.parse(aaf)

    flat = aaf_ingest.flatten_audio(source, tmp_path / "work")

    data = _samples(flat)
    assert abs(len(data) / aaf_ingest.SAMPLE_RATE - source.duration_s) < 0.05
    assert float(np.max(np.abs(data))) == 0.0


def test_a_sequence_with_no_clips_at_all_flattens_to_its_own_length(tmp_path):
    """It used to hand ffmpeg an empty concat list and die on the exit code.

    `samples/Gugu human cat.aaf` is one: a sequence whose tracks hold nothing
    the parser recognises as a clip. Silence of the right length is the honest
    answer — the stage after this one then finds no speech and says so, which
    is a diagnosis rather than a stack trace.
    """
    aaf = _linked_aaf(tmp_path / "export", (440,), seconds=2.0)
    source = aaf_ingest.parse(aaf)
    source.clips = []
    out_dir = tmp_path / "work"

    flat = aaf_ingest.flatten_audio(source, out_dir)

    seconds = len(_samples(flat)) / aaf_ingest.SAMPLE_RATE
    assert abs(seconds - source.duration_s) < 0.05
    assert float(np.max(np.abs(_samples(flat)))) == 0.0


def test_the_microphones_survive_the_mix_for_speaker_attribution(tmp_path):
    """Mixing must not cost the free attribution multi-track material has.

    Each per-track render is one microphone at the sequence's full length, on
    the same timeline as the mix, which is what `speakers.attribute_from_files`
    takes. Without these, a four-microphone podcast would be mixed to one file
    and then reported as material whose voices were never separated.
    """
    aaf = _linked_aaf(tmp_path / "export", (300, 900, 1500, 2100))
    source = aaf_ingest.parse(aaf)
    out_dir = tmp_path / "work"

    aaf_ingest.flatten_audio(source, out_dir)
    mics = aaf_ingest.track_renders(source, out_dir)

    assert sorted(mics) == [0, 1, 2, 3]
    # One tone each, so the loudest mic per word is a real question with a real
    # answer — which is the whole basis of attribution by microphone.
    for track, hz in zip(sorted(mics), (300, 900, 1500, 2100)):
        assert _energy_at(mics[track], hz) > 0.5


def test_one_track_leaves_no_microphones_to_attribute_from(tmp_path):
    """A single-track sequence has no mic set, and must not pretend to."""
    aaf = _linked_aaf(tmp_path / "export", (440,))
    source = aaf_ingest.parse(aaf)
    out_dir = tmp_path / "work"

    aaf_ingest.flatten_audio(source, out_dir)

    assert aaf_ingest.track_renders(source, out_dir) == {}


def test_a_gap_and_an_unresolved_clip_get_different_silence_files(tmp_path):
    """They used to be able to collide, and the second write shortened the first.

    Two counters wrote into one `_sil_{idx}` namespace: a clip index and a
    parts-length. When they met, one silence took the other's duration and the
    flattened audio drifted out of step with the timeline it describes.
    """
    out_dir = tmp_path / "work"
    out_dir.mkdir()

    gap = aaf_ingest._silence(out_dir, "00_g00003", 1.0)
    clip = aaf_ingest._silence(out_dir, "00_c00003", 2.0)

    assert gap != clip
    assert len(_samples(gap)) == aaf_ingest.SAMPLE_RATE
    assert len(_samples(clip)) == 2 * aaf_ingest.SAMPLE_RATE
