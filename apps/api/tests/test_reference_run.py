"""The runner produces what `run.py` produces. On real footage.

`run.py` is the specification: whatever the orchestrator does, it must turn the
same input into the same artifacts. Everything else in this suite tests
orchestration with the pipeline stubbed out; this is the one that runs the
pipeline for real and compares the output byte for byte.

It needs a sample, which is 70 MB of somebody's actual footage and is not in the
repository. Point it at one:

    MISHNE_SAMPLE_AAF=samples/SyncDaniel.aaf \\
    MISHNE_SAMPLE_REPLAY=samples/SyncDaniel_roughcut/work/SyncDaniel_flat_a0.asr.json \\
    .venv/bin/python -m pytest tests/test_reference_run.py -q

`--replay` is what makes it a ten-second test rather than a transcription: the
stored ASR response is replayed, so no model is loaded and no network is
touched, and every stage after transcription runs for real.

**AAF is excluded from the byte comparison, deliberately.** Two runs of the same
code produce two different AAF files of identical size: the format carries
generated MobIDs and a modification date. That is a property of the format, not
of this pipeline, and it is why `validate` reads an artifact back and compares
the timeline rather than comparing bytes.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

API_ROOT = Path(__file__).parent.parent

SAMPLE = os.environ.get("MISHNE_SAMPLE_AAF", "")
REPLAY = os.environ.get("MISHNE_SAMPLE_REPLAY", "")

requires_sample = pytest.mark.skipif(
    not (SAMPLE and Path(SAMPLE).exists() and REPLAY and Path(REPLAY).exists()),
    reason=(
        "no sample — set MISHNE_SAMPLE_AAF and MISHNE_SAMPLE_REPLAY to a real "
        "AAF and a stored ASR response"
    ),
)

pytestmark = requires_sample

#: Compared byte for byte. The AAF is not, for the reason in the module
#: docstring; `validate` is what checks it, by reading it back.
COMPARED = (
    "{stem}.edl",
    "{stem}.fcpxml",
    "{stem}.otio",
    "{stem}.transcript.html",
    "{stem}.mishne.json",
)

TARGET = "40s"
STEM = "SyncDaniel"


@pytest.fixture(scope="module")
def reference(tmp_path_factory) -> Path:
    """What `run.py` produces. The regression target for the whole workstream."""
    out = tmp_path_factory.mktemp("reference")
    result = subprocess.run(
        [
            sys.executable, str(API_ROOT / "run.py"), SAMPLE,
            "--out", str(out), "--replay", REPLAY, "--target", TARGET,
            "--scorer", "heuristic", "--spans", "enumerate",
        ],
        capture_output=True, text=True, cwd=str(API_ROOT),
    )
    assert result.returncode == 0, result.stdout[-3000:] + result.stderr[-2000:]
    return out


@pytest.fixture(scope="module")
def through_the_runner(tmp_path_factory) -> tuple[Path, object]:
    """The same job, through the orchestrator."""
    from mishne.orchestration import AssetSource, JobRequest, RecordingSink, run_job

    out = tmp_path_factory.mktemp("runner")
    sink = RecordingSink()
    result = run_job(
        JobRequest(
            job_id="job_reference",
            org_id="org_reference",
            project_id="prj_reference",
            assets=[AssetSource(asset_id="ast_reference", path=Path(SAMPLE))],
            out_dir=out,
            work_dir=tmp_path_factory.mktemp("work"),
            target_duration_s=40,
            scorer="heuristic",
            spans="enumerate",
            replay=Path(REPLAY),
            stem=STEM,
        ),
        sink,
    )
    return out, (result, sink)


@pytest.mark.parametrize("name", COMPARED)
def test_the_runner_produces_the_same_artifact_as_run_py(
    name, reference, through_the_runner
):
    out, _ = through_the_runner
    expected = (reference / name.format(stem=STEM)).read_bytes()
    actual = (out / name.format(stem=STEM)).read_bytes()
    assert actual == expected, f"{name} differs from the reference run"


def test_the_aaf_is_written_and_validates_even_though_its_bytes_differ(
    reference, through_the_runner
):
    out, (result, _) = through_the_runner
    ours = out / f"{STEM}.aaf"
    theirs = reference / f"{STEM}.aaf"

    assert ours.exists() and theirs.exists()
    # Same size, different bytes: generated MobIDs and a modification date.
    assert ours.stat().st_size == theirs.stat().st_size
    # `validate` read it back and compared it to the timeline, which is the
    # check that actually means something for this format.
    assert all(c.ok for c in result.state.checks)


def test_every_stage_ran_in_order_and_reported_something(through_the_runner):
    from mishne.pipeline.steps import STEP_NAMES

    _out, (result, sink) = through_the_runner
    assert [s.name for s in result.steps] == STEP_NAMES
    assert all(s.status == "done" for s in sink.steps)
    assert all(s.detail for s in sink.steps)


def test_the_shape_of_the_cut_is_what_the_brief_predicts(through_the_runner):
    """23 beats, 30 candidates, 10 spans.

    Was 25 candidates when `CARVE_ABOVE_MS` was 12s. At 8s, seven beats are
    carved rather than two — an 8-12s answer is 7-10% of a 120s piece and
    should not be a take-it-or-leave-it candidate.

    And 4 picks became 10 once no single clip could exceed its share of the
    target. The extra offers had been there since the carving change and went
    unused: the objective is quality-weighted screen time under a fixed
    duration, which is indifferent to how that time is divided, so a long block
    that scored well took the budget. Same material, same seconds, two and a
    half times the clips.
    """
    _out, (result, _) = through_the_runner
    state = result.state
    assert len(state.assets) == 1
    assert len(state.beats) == 23
    assert len(state.candidates) == 30
    assert len(state.picks) == 10
    assert len(state.artifacts) == 4
    assert all(a.ok for a in state.artifacts)


def test_a_second_run_transcribes_nothing(tmp_path_factory, through_the_runner):
    """The economics of multi-upload, on real material (ADR-0008).

    The same asset, a new job, a shared work directory: the per-asset phase is
    served from the content-addressed cache and transcription does not happen.
    """
    from mishne.orchestration import AssetSource, JobRequest, RecordingSink, run_job

    work = tmp_path_factory.mktemp("shared-work")
    kwargs = dict(
        org_id="org_reference", project_id="prj_reference",
        assets=[AssetSource(asset_id="ast_reference", path=Path(SAMPLE))],
        work_dir=work, target_duration_s=40, scorer="heuristic",
        spans="enumerate", replay=Path(REPLAY), stem=STEM,
    )
    first = run_job(
        JobRequest(job_id="job_first", out_dir=tmp_path_factory.mktemp("first"), **kwargs)
    )
    sink = RecordingSink()
    run_job(
        JobRequest(job_id="job_second", out_dir=tmp_path_factory.mktemp("second"), **kwargs),
        sink,
    )

    assert not first.state.runs["ast_reference"].from_cache
    transcribe = [s for s in sink.steps if s.name == "transcribe"][0]
    assert transcribe.detail == "cached"
    assert all(
        s.detail == "cached" for s in sink.steps
        if s.name in ("prepare", "audio", "transcribe", "vad", "structure", "speakers")
    )
