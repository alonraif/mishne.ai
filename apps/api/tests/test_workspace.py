"""The workspace: objects in, real files on disk, derived files back out.

The reason this module exists is that ffmpeg takes `argv` and pyaaf2 seeks
around inside structured storage, so neither can be handed a stream (ADR-0013).
These tests are therefore about files actually being on a disk, with the right
names, in the right directory — not about calls being made.
"""

from __future__ import annotations

from pathlib import Path

import pytest

boto3 = pytest.importorskip("boto3")
moto = pytest.importorskip("moto")

from mishne import storage, workspace  # noqa: E402
from mishne.config import Settings  # noqa: E402

REGION = "eu-west-1"
ORG = "org_7fa2"
PROJECT = "prj_promo"


@pytest.fixture
def settings() -> Settings:
    return Settings(
        environment="local",
        aws_region=REGION,
        s3_bucket_raw="test-raw",
        s3_bucket_derived="test-derived",
        s3_bucket_artifacts="test-artifacts",
    )


@pytest.fixture
def aws(monkeypatch):
    for name in ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN"):
        monkeypatch.setenv(name, "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", REGION)
    with moto.mock_aws():
        yield


@pytest.fixture
def store(aws, settings) -> storage.Storage:
    client = boto3.client("s3", region_name=REGION)
    for bucket in ("test-raw", "test-derived", "test-artifacts"):
        client.create_bucket(
            Bucket=bucket, CreateBucketConfiguration={"LocationConstraint": REGION}
        )
    return storage.Storage(settings, client=client)


def _workspace(store, settings, scratch: Path) -> workspace.S3Workspace:
    return workspace.S3Workspace(
        org_id=ORG, project_id=PROJECT, scratch=scratch, storage=store, settings=settings
    )


def _put_source(store, settings, asset_id: str, blob: bytes) -> storage.ObjectRef:
    ref = storage.ObjectRef(
        storage.bucket_for("raw", settings), storage.source_key(ORG, PROJECT, asset_id)
    )
    store.put_bytes(blob, ref)
    return ref


# ─────────────────────────────────────────────────────────────────── the local path


def test_the_local_workspace_hands_back_the_path_it_was_given(tmp_path: Path):
    # The concierge CLI must stay exactly as cheap as it was: nothing uploaded,
    # nothing fetched, no cloud dependency on a laptop.
    ws = workspace.LocalWorkspace(tmp_path)
    source = workspace.SourceFile(name="SyncDaniel.aaf", ref=storage.ObjectRef("", "/media/a.aaf"))
    assert ws.materialise("ast_1", source) == Path("/media/a.aaf")
    assert ws.publish_asset("ast_1") is None
    assert ws.publish_artifact(tmp_path / "cut.aaf", "job_1", "cut.aaf") is None
    assert ws.asset_dir("ast_1").is_dir()


# ───────────────────────────────────────────────────────────── materialising sources


def test_materialise_puts_the_source_on_disk_under_its_own_name(store, settings, tmp_path):
    ref = _put_source(store, settings, "ast_1", b"rushes")
    ws = _workspace(store, settings, tmp_path / "job_1")

    path = ws.materialise("ast_1", workspace.SourceFile(name="A002.mxf", ref=ref))

    assert path.is_file()
    assert path.read_bytes() == b"rushes"
    assert path.name == "A002.mxf"


def test_companions_land_beside_the_aaf_under_their_original_names(store, settings, tmp_path):
    # This is what makes a LINKED aaf resolve: aaf_ingest looks for the
    # referenced basenames in the same directory, and knows nothing about S3.
    aaf = _put_source(store, settings, "ast_seq", b"AAF")
    media = storage.ObjectRef(storage.bucket_for("raw", settings), "orgs/o/p/companion/source")
    store.put_bytes(b"MXF", media)
    ws = _workspace(store, settings, tmp_path / "job_1")

    path = ws.materialise(
        "ast_seq",
        workspace.SourceFile(name="SyncDaniel.aaf", ref=aaf),
        companions=[workspace.SourceFile(name="A001C002.mxf", ref=media)],
    )

    assert (path.parent / "A001C002.mxf").read_bytes() == b"MXF"


def test_a_filename_from_a_browser_cannot_escape_the_scratch_tree(store, settings, tmp_path):
    ref = _put_source(store, settings, "ast_1", b"rushes")
    ws = _workspace(store, settings, tmp_path / "job_1")

    path = ws.materialise("ast_1", workspace.SourceFile(name="../../etc/passwd", ref=ref))

    assert tmp_path in path.parents
    assert ".." not in str(path)


def test_an_already_materialised_source_is_not_downloaded_twice(store, settings, tmp_path):
    ref = _put_source(store, settings, "ast_1", b"rushes")
    ws = _workspace(store, settings, tmp_path / "job_1")
    first = ws.materialise("ast_1", workspace.SourceFile(name="A002.mxf", ref=ref))
    first.write_bytes(b"locally modified")

    again = ws.materialise("ast_1", workspace.SourceFile(name="A002.mxf", ref=ref))

    assert again == first
    assert again.read_bytes() == b"locally modified"


# ─────────────────────────────────────────────────────────────────────── the cache


def test_the_cache_carries_the_expensive_files_and_not_the_scratch(store, settings, tmp_path):
    ws = _workspace(store, settings, tmp_path / "job_1")
    d = ws.asset_dir("ast_1")
    (d / "ingest.json").write_text("{}")
    (d / "audio.wav").write_bytes(b"WAV")
    (d / "run.asr.json").write_text("{}")
    # Intermediates: reproducible in seconds, and there can be thousands.
    (d / "_seg_0001.wav").write_bytes(b"SEG")
    (d / "_concat.txt").write_text("x")

    ws.publish_asset("ast_1")

    prefix = storage.derived_key(ORG, PROJECT, "ast_1", "")
    listed = store.client.list_objects_v2(
        Bucket=storage.bucket_for("derived", settings), Prefix=prefix
    )
    names = sorted(o["Key"][len(prefix):] for o in listed.get("Contents", []))
    assert names == ["audio.wav", "ingest.json", "run.asr.json"]


def test_a_second_worker_gets_the_cache_back(store, settings, tmp_path):
    # This is the economics: transcription is never repaid because the cache
    # outlives the container that built it.
    first = _workspace(store, settings, tmp_path / "job_1")
    (first.asset_dir("ast_1") / "ingest.json").write_text('{"beats": 23}')
    first.publish_asset("ast_1")

    second = _workspace(store, settings, tmp_path / "job_2")
    cached = second.asset_dir("ast_1") / "ingest.json"

    assert cached.read_text() == '{"beats": 23}'


def test_hydration_never_overwrites_what_this_run_has_already_written(store, settings, tmp_path):
    first = _workspace(store, settings, tmp_path / "job_1")
    (first.asset_dir("ast_1") / "ingest.json").write_text("stale")
    first.publish_asset("ast_1")

    second = _workspace(store, settings, tmp_path / "job_2")
    d = second.root / "assets" / "ast_1"
    d.mkdir(parents=True)
    (d / "ingest.json").write_text("fresh")

    assert (second.asset_dir("ast_1") / "ingest.json").read_text() == "fresh"


def test_a_failed_cache_upload_does_not_take_the_job_down(store, settings, tmp_path, monkeypatch):
    ws = _workspace(store, settings, tmp_path / "job_1")
    (ws.asset_dir("ast_1") / "ingest.json").write_text("{}")

    def boom(*_args, **_kwargs):
        raise RuntimeError("s3 is having a day")

    monkeypatch.setattr(store, "upload", boom)
    ws.publish_asset("ast_1")  # logged, not raised


# ───────────────────────────────────────────────────────────────────── artifacts


def test_an_artifact_is_published_where_the_download_path_will_look(store, settings, tmp_path):
    ws = _workspace(store, settings, tmp_path / "job_1")
    local = tmp_path / "cut.aaf"
    local.write_bytes(b"AAF")

    ref = ws.publish_artifact(local, "job_1", "cut.aaf")

    assert ref.bucket == storage.bucket_for("artifacts", settings)
    assert ref.key == storage.artifact_key(ORG, "job_1", "cut.aaf")
    assert store.get_bytes(ref) == b"AAF"


def test_cleanup_removes_the_scratch_tree(store, settings, tmp_path):
    ws = _workspace(store, settings, tmp_path / "job_1")
    (ws.asset_dir("ast_1") / "ingest.json").write_text("{}")

    ws.cleanup()

    assert not (tmp_path / "job_1").exists()


# ── the orchestrator has to actually call it ───────────────────────────────


def test_the_orchestrator_publishes_the_ingest_cache_it_just_paid_for(
    store, settings, tmp_path, monkeypatch
):
    """Every test above proves the workspace *can* keep the cache. None proved
    the pipeline asks it to.

    `project.finish_ingest` takes the workspace as an optional third argument
    and the last per-asset step was not passing it, so on a worker the ingest
    cache was written to scratch that `worker.execute` deletes at the end of
    the job. ADR-0008 was true of `run.py` and false of the product: every
    orchestrated job re-transcribed from cold, and the derived bucket of a
    system that had run jobs was empty — which is what gave it away.
    """
    from mishne.orchestration import graph
    from mishne.pipeline import project
    from mishne.timecode import Rate

    ws = _workspace(store, settings, tmp_path / "job_1")
    request = graph.JobRequest(
        job_id="job_1", org_id=ORG, project_id=PROJECT,
        assets=[graph.AssetSource(asset_id="ast_1", path=tmp_path / "a.mov",
                                  content_id="a_deadbeef")],
        out_dir=tmp_path / "out", work_dir=ws,
    )
    state = graph.RunState(request=request)
    assert state.workspace is ws          # …and a bare path yields None:
    bare = graph.RunState(request=graph.JobRequest(
        job_id="j", org_id=ORG, project_id=PROJECT, assets=[],
        out_dir=tmp_path, work_dir=tmp_path))
    assert bare.workspace is None

    run = graph.AssetRun(source=request.assets[0],
                         adir=ws.asset_dir("a_deadbeef"))
    run.tracks = [type("T", (), {"path": run.adir / "audio.wav"})()]
    run.asr = type("A", (), {"language": "en"})()
    run.prepared = type("P", (), {
        "info": type("I", (), {"rate": Rate(25, 1, False), "start_tc_frames": 0,
                               "duration_frames": 250})(),
        "aaf": None, "provenance": "rushes", "seams": [], "notes": [],
    })()
    state.runs["ast_1"] = run
    state.current = "ast_1"

    monkeypatch.setattr(project, "stage_speakers",
                        lambda *a, **k: type("Att", (), {"speakers": []})())
    monkeypatch.setattr(project, "stage_structure", lambda *a, **k: ([], []))
    ctx = graph.StepContext(job_id="job_1", org_id=ORG, project_id=PROJECT)
    # Both, in the registry's order: `structure` is the last per-asset step and
    # therefore the one that writes the cache, and it needs the attribution
    # `speakers` put on the run.
    graph.step_speakers(ctx, state)
    graph.step_structure(ctx, state)

    prefix = storage.derived_key(ORG, PROJECT, "a_deadbeef", "")
    listed = store.client.list_objects_v2(
        Bucket=storage.bucket_for("derived", settings), Prefix=prefix)
    names = sorted(o["Key"][len(prefix):] for o in listed.get("Contents", []))
    assert "ingest.json" in names
