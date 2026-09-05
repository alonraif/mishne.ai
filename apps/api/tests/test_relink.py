"""What an artifact says about where the media is.

The failure this guards against is the one a customer sees first and cannot
work around: they download four deliverables and three of them will not
reconnect to their rushes. Only the EDL did, and only because CMX3600 carries a
reel name and a timecode and no path at all — it was passing for the wrong
reason, not because the pipeline was getting media identity right.

Two independent breaks, and the tests below are split along them because either
one alone is fatal:

* the artifacts named the worker's scratch copy, at a path that never existed
  on the customer's machine and is deleted when the job ends;
* they named it under `workspace._safe_name`'s sanitised filename, so even
  "find me a file called that" — Premiere's locate dialog, Avid's relink by
  source file name — was looking for the wrong name.
"""

from __future__ import annotations

import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import quote, unquote

import aaf2
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from mishne.orchestration.graph import AssetSource, JobRequest  # noqa: E402
from mishne.pipeline.steps import aaf_ingest, assemble, emit  # noqa: E402
from mishne.pipeline.steps.refine import Cut  # noqa: E402
from mishne.timecode import Rate, tc_to_frames  # noqa: E402

PAL = Rate(25, 1)

#: The name that broke it: an apostrophe, a comma and parentheses, all of which
#: `_safe_name` replaces with underscores on the way to disk.
CUSTOMER_NAME = "RUSHES Tia Mowry talks 'My Next Act,' - AP Archive (360p, h264).mp4"
STAGED_NAME = "RUSHES Tia Mowry talks _My Next Act__ - AP Archive _360p_ h264_.mp4"


def cuts() -> list[Cut]:
    start = tc_to_frames(1, 0, 0, 0, PAL)
    return [
        Cut(beat_id="a_beat_0001", asset_id="a_deadbeef", order_idx=0,
            src_in=start + 100, src_out=start + 350, speaker="spk_1",
            text="first"),
        Cut(beat_id="a_beat_0002", asset_id="a_deadbeef", order_idx=1,
            src_in=start + 900, src_out=start + 1200, speaker="spk_2",
            text="second"),
    ]


@pytest.fixture
def staged_asset(tmp_path: Path) -> assemble.AssetRef:
    """An asset exactly as a worker hands it to stage 10.

    The path is a scratch copy under the sanitised name — the arrangement
    `S3Workspace.materialise` produces and `workspace.cleanup()` destroys.
    """
    scratch = tmp_path / "mishne" / "job_31a52ac8" / "sources" / "a0d804d3"
    scratch.mkdir(parents=True)
    staged = scratch / STAGED_NAME
    staged.write_bytes(b"not really an mp4")
    return assemble.AssetRef(
        rate=PAL, start_tc_frames=tc_to_frames(1, 0, 0, 0, PAL),
        duration_frames=25 * 600, asset_id="a_deadbeef",
        media_path=staged, display_name=CUSTOMER_NAME, staged=True,
        width=640, height=360)


@pytest.fixture
def artifacts(staged_asset, tmp_path: Path) -> dict[str, Path]:
    timeline = assemble.build_multi(cuts(), {"a_deadbeef": staged_asset},
                                    name="job_roughcut")
    written = emit.emit(timeline, tmp_path / "out", "job_roughcut")
    failed = [f"{a.fmt}: {a.error}" for a in written if not a.ok]
    assert not failed, failed
    return {a.kind: a.path for a in written}


# --- the scratch path, in each format that used to carry it ------------------


def test_no_artifact_names_the_scratch_directory(artifacts, tmp_path):
    """The strongest form of the check: the path is simply not in the bytes.

    Deliberately a substring search over the raw file rather than a parse of
    each format. The path leaked into the FCPXML `src`, the AAF's
    NetworkLocator and the EDL's `FROM CLIP` comment — three different writers
    reading one `target_url` — and a per-format assertion would keep passing
    while a fourth found somewhere new to put it.
    """
    scratch = str(tmp_path).encode()
    for kind, path in artifacts.items():
        blob = path.read_bytes()
        assert scratch not in blob, f"{kind} names the scratch directory"
        # The AAF stores strings as UTF-16, so the ASCII search above would
        # miss it there.
        assert scratch.decode().encode("utf-16-le") not in blob, \
            f"{kind} names the scratch directory"


def test_no_artifact_carries_the_sanitised_filename(artifacts):
    """`_safe_name`'s output belongs on disk and nowhere else."""
    mangled = "_My Next Act__"
    for kind, path in artifacts.items():
        blob = path.read_bytes()
        assert mangled.encode() not in blob, f"{kind} carries the staged name"
        assert mangled.encode("utf-16-le") not in blob, \
            f"{kind} carries the staged name"


# --- what each format says instead -------------------------------------------


def test_fcpxml_asset_src_is_the_customers_filename(artifacts):
    root = ET.parse(artifacts["fcpxml"]).getroot()
    assets = root.findall(".//asset")
    assert len(assets) == 1
    assert unquote(assets[0].get("src")) == CUSTOMER_NAME
    # Relative, so it resolves against the document when the two sit together.
    assert "://" not in assets[0].get("src")


def test_fcpxml_format_states_the_real_raster(artifacts):
    """The adapter probes `src` for this and would find nothing to probe.

    Without `fcpx_patch._patch_format_name` the format element loses its name
    entirely the moment the artifact stops naming a local file.
    """
    root = ET.parse(artifacts["fcpxml"]).getroot()
    names = {f.get("name") for f in root.findall(".//format")}
    assert names == {"FFVideoFormat640x360p25.0"}


def test_edl_reel_and_from_clip_are_the_customers(artifacts):
    text = artifacts["edl"].read_text()
    assert f"FROM CLIP: {CUSTOMER_NAME}" in unquote(text)
    # The reel is the relink key an EDL has instead of a path, and it is
    # truncated to eight characters by the format, not by us.
    assert "RUSHES T" in text


def test_aaf_tape_and_master_mobs_carry_the_customers_name(artifacts):
    """The two mobs Avid relinks against, and what each is for.

    The MasterMob is what lands in the bin as a clip; the TapeMob behind it is
    what *relink by source file name* and *by tape name* match on. The physical
    file mobs between them are left unnamed by the adapter and Avid does not
    look at their names, so they are not asserted here.
    """
    stem = Path(CUSTOMER_NAME).stem
    with aaf2.open(str(artifacts["aaf"])) as f:
        masters = [m for m in f.content.mobs
                   if isinstance(m, aaf2.mobs.MasterMob)]
        tapes = [m for m in f.content.mobs
                 if isinstance(m, aaf2.mobs.SourceMob)
                 and isinstance(m.descriptor, aaf2.essence.TapeDescriptor)]
        assert masters, "no MasterMob to relink against"
        assert tapes, "no TapeMob to relink by name against"
        for mob in masters + tapes:
            assert mob.name.startswith(stem), mob.name


def test_aaf_descriptor_describes_the_actual_media(artifacts):
    """`otio_aaf_adapter` defaults an unknown raster to 1920x1080, silently."""
    with aaf2.open(str(artifacts["aaf"])) as f:
        cdci = [m.descriptor for m in f.content.mobs
                if isinstance(m, aaf2.mobs.SourceMob)
                and isinstance(m.descriptor, aaf2.essence.CDCIDescriptor)]
        assert cdci, "no picture descriptor in the AAF"
        for d in cdci:
            assert d["StoredWidth"].value == 640
            assert d["StoredHeight"].value == 360
            assert str(d["ImageAspectRatio"].value) == "16/9"


def test_the_mob_id_is_the_content_hash_not_the_filename(staged_asset):
    """Rename the upload and the MobID must not move.

    The relink key has to survive the customer renaming a file, and it has to
    be the same for the same rushes across every job — that is what makes a
    re-cut drop into the bin the last one relinked into. It used to be derived
    from the filename, which meant `_safe_name` changing its mind about
    punctuation would silently reissue every ID in the system.
    """
    renamed = assemble.AssetRef(
        rate=staged_asset.rate, start_tc_frames=staged_asset.start_tc_frames,
        duration_frames=staged_asset.duration_frames,
        asset_id=staged_asset.asset_id, media_path=staged_asset.media_path,
        display_name="something else entirely.mp4", staged=True)

    def mob_id(asset):
        tl = assemble.build_multi(cuts(), {"a_deadbeef": asset})
        clip = tl.tracks[0].find_clips()[0]
        return clip.media_reference.metadata["AAF"]["MobID"]

    assert mob_id(staged_asset) == mob_id(renamed)


# --- the local path is still written when it is genuinely the customer's -----


def test_a_local_run_still_names_the_real_file(tmp_path):
    """`run.py` read the customer's own file off the customer's own disk.

    Throwing that away would be a regression for the concierge path: an
    absolute URL there relinks with no dialog at all, and the file it names is
    one that will still be there tomorrow.
    """
    media = tmp_path / CUSTOMER_NAME
    media.write_bytes(b"not really an mp4")
    asset = assemble.AssetRef(
        rate=PAL, start_tc_frames=tc_to_frames(1, 0, 0, 0, PAL),
        duration_frames=25 * 600, asset_id="a_deadbeef", media_path=media)

    timeline = assemble.build_multi(cuts(), {"a_deadbeef": asset})
    ref = timeline.tracks[0].find_clips()[0].media_reference
    assert ref.target_url == media.resolve().as_uri()


# --- the name of the sequence itself -----------------------------------------


def test_the_sequence_is_not_called_roughcut_twice():
    """`run.py` and the worker disagree about what `stem` already contains."""
    common = dict(job_id="j", org_id="o", project_id="p", assets=[],
                  out_dir=Path("/tmp"), work_dir=Path("/tmp"))
    assert JobRequest(stem="interview", **common).timeline_name \
        == "interview_roughcut"
    assert JobRequest(stem="interview_roughcut", **common).timeline_name \
        == "interview_roughcut"


def test_the_request_reports_the_customers_names_for_staged_assets():
    source = AssetSource(asset_id="ast_1", path=Path("/scratch/mangled.mp4"),
                         content_id="a_deadbeef", display_name=CUSTOMER_NAME)
    request = JobRequest(job_id="j", org_id="o", project_id="p",
                         assets=[source], out_dir=Path("/tmp"),
                         work_dir=Path("/tmp"))
    assert request.media_names == {"a_deadbeef": CUSTOMER_NAME}
    assert request.media_is_staged

    local = AssetSource(asset_id="ast_1", path=Path("/rushes/real.mp4"),
                        content_id="a_deadbeef")
    request.assets = [local]
    assert request.media_names == {}
    assert not request.media_is_staged


# --- the same job, when the upload was an AAF --------------------------------


def aaf_source(scratch: Path, companion: str) -> aaf_ingest.AAFSource:
    """A linked AAF as the worker has it: sequence and media both staged.

    Both files are on disk under `_safe_name`'s versions of their names —
    materialising the companion beside the sequence under its *own* name is what
    resolves a linked AAF (ADR-0014), and "its own name" is the sanitised one.
    The locator inside the AAF still spells the original.
    """
    staged = scratch / companion
    staged.write_bytes(b"not really an mxf")
    clip = aaf_ingest.SourceClip(
        index=0, name="A001 shot 3", mob_id=AVID_MOB_ID,
        source_mob_id=AVID_MOB_ID, media_path=staged,
        embedded_mob_id=None, src_in=0, src_out=25 * 600, src_rate=25.0,
        origin=0, tl_in=0, tl_out=25 * 600,
        target_url=f"file:///Volumes/SAN/Rushes/{quote(AAF_COMPANION)}")
    return aaf_ingest.AAFSource(
        path=scratch / "sequence.aaf", rate=PAL, duration_frames=25 * 600,
        start_tc_frames=0, clips=[clip])


AVID_MOB_ID = ("urn:smpte:umid:060a2b34.01010105.01010f20.13000000."
               "aabbccdd.11223344.55667788.99aabbcc")
#: The companion as the editor named it; `_safe_name` gives the second.
AAF_COMPANION = "A001 'take 3', best (v2).mxf"
AAF_COMPANION_STAGED = "A001 _take 3__ best _v2_.mxf"


@pytest.fixture
def aaf_artifacts(tmp_path: Path) -> dict[str, Path]:
    scratch = tmp_path / "mishne" / "job_aaf" / "sources" / "a0d804d3"
    scratch.mkdir(parents=True)
    asset = assemble.AssetRef(
        rate=PAL, start_tc_frames=0, duration_frames=25 * 600,
        asset_id="a_deadbeef", aaf=aaf_source(scratch, AAF_COMPANION_STAGED),
        display_name="EP3 rough v4.aaf", staged=True)
    cut = Cut(beat_id="a_beat_0001", asset_id="a_deadbeef", order_idx=0,
              src_in=100, src_out=350, speaker="spk_1", text="first")
    timeline = assemble.build_multi([cut], {"a_deadbeef": asset},
                                    name="job_roughcut")
    written = emit.emit(timeline, tmp_path / "out", "job_roughcut")
    failed = [f"{a.fmt}: {a.error}" for a in written if not a.ok]
    assert not failed, failed
    return {a.kind: a.path for a in written}


def test_an_aaf_upload_keeps_the_editors_own_mob_id(aaf_artifacts):
    """The relink key for an AAF source is inherited, not synthesised.

    This is the part that was already right and the part that matters most: the
    output drops into the bin the sequence came from and resolves against media
    Avid already knows, with no dialog. Asserted here so the media-naming work
    below cannot quietly replace it with a mishne-issued ID.
    """
    with aaf2.open(str(aaf_artifacts["aaf"])) as f:
        ids = {str(m.mob_id) for m in f.content.mobs}
    assert AVID_MOB_ID in ids


def test_an_aaf_upload_names_its_companions_as_the_editor_does(aaf_artifacts,
                                                               tmp_path):
    """The locator, not the staged copy — a second sanitised name to undo.

    A linked AAF has two filenames in play and both went through `_safe_name`:
    the sequence itself, and every companion materialised beside it. The
    sequence's real name arrives as `display_name` like any other upload, but a
    companion's does not — the only record of it is the locator inside the AAF,
    which is what stage 10 now reads.
    """
    for kind, path in aaf_artifacts.items():
        blob = path.read_bytes()
        for probe in (str(tmp_path), "_take 3__"):
            assert probe.encode() not in blob, f"{kind}: {probe}"
            assert probe.encode("utf-16-le") not in blob, f"{kind}: {probe}"

    root = ET.parse(aaf_artifacts["fcpxml"]).getroot()
    srcs = {unquote(a.get("src")) for a in root.findall(".//asset")}
    assert srcs == {AAF_COMPANION}
