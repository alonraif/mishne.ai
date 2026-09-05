"""The registry describes what the pipeline runs. All three readers depend on it.

The state machine is generated from `STEPS`, the runner executes `STEPS`, and
the progress UI renders `STEPS`. Until B3 the list was wrong in both directions:
it omitted `speakers`, the AAF branch, span proposal and the transcript page,
and it listed `review` — a stage that was designed, never built, and would have
become a state in the generated machine that nothing could ever run.

These tests are cheap and they are the reason that cannot come back.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from mishne.pipeline.steps import (  # noqa: E402
    ASSET_STEPS,
    JOB_STEPS,
    STEP_NAMES,
    STEPS,
    STEPS_BY_NAME,
)


def test_every_step_has_an_implementation_and_every_implementation_a_step():
    from mishne.orchestration.graph import IMPLEMENTATIONS

    assert set(IMPLEMENTATIONS) == set(STEP_NAMES), (
        "a name in one and not the other is either a phantom state in the "
        "machine or a stage nobody can execute"
    )


def test_there_is_no_review_stage_anywhere():
    # It was a coherence pass feeding constraints back to the solver, bounded at
    # two iterations, and it was never built. A stub in the registry is how a
    # state machine ends up with a state that always fails.
    assert "review" not in STEPS_BY_NAME
    assert not (
        Path(__file__).parent.parent / "src" / "mishne" / "pipeline" / "steps" / "review.py"
    ).exists()


def test_the_registry_covers_what_run_py_imports():
    """`run.py` is the specification. Every stage it calls is in the list."""
    source = (Path(__file__).parent.parent / "run.py").read_text()
    for name in ("brief", "propose", "score", "select", "refine", "assemble",
                 "emit", "validate", "transcript_page"):
        assert name in source, f"run.py no longer mentions {name}"
        assert name in STEPS_BY_NAME, f"{name} runs in run.py but is not in STEPS"


def test_the_per_asset_phase_is_the_cached_one():
    # Stages 0-4 plus speakers are keyed on the asset's content and survive
    # every re-run (ADR-0008). Everything else is per job.
    assert [s.name for s in ASSET_STEPS] == [
        "prepare", "audio", "transcribe", "vad", "speakers", "structure",
    ]
    assert JOB_STEPS[0].name == "brief"
    assert JOB_STEPS[-1].name == "transcript_page"


def test_speakers_runs_before_structure_in_the_registry_and_in_the_driver():
    """The one per-asset ordering that is a data dependency rather than taste.

    Attribution rewrites the speaker on every word in place, and a beat takes
    the speaker of its first word. `speakers` after `structure` is therefore not
    a different order, it is a different result: every job the orchestrator ran
    came out with beats labelled `c0:spk:0` from the ASR vendor while the
    speaker legend offered `T0`/`T1` from the microphones — two id spaces, so the
    UI showed a raw vendor id on every line, one colour for everybody, and a
    speaker filter that matched nothing.

    `project.ingest` had it right throughout, and the registry did not. Both
    ends are asserted here because fixing either alone leaves the bug: the
    registry is what the orchestrator executes, and the driver is the
    specification it must agree with.

    The other per-asset stages are deliberately not pinned. `vad` and
    `transcribe` are independent and the two drivers run them in opposite
    orders, which costs nothing.
    """
    names = [s.name for s in ASSET_STEPS]
    assert names.index("speakers") < names.index("structure")

    source = (
        Path(__file__).parent.parent / "src" / "mishne" / "pipeline" / "project.py"
    ).read_text()
    driver = source.split("\ndef ingest(", 1)[1].split("\ndef _save(", 1)[0]
    assert driver.index("stage_speakers(") < driver.index("stage_structure(")


def test_the_expensive_stages_retry_and_the_deterministic_ones_do_not():
    # A provider returning 503 is not a reason to fail somebody's job. A
    # validation failure means an artifact is wrong, and writing it again
    # produces the same wrong artifact.
    assert STEPS_BY_NAME["transcribe"].retries > 0
    assert STEPS_BY_NAME["score"].retries > 0
    assert STEPS_BY_NAME["assemble"].retries == 0
    assert STEPS_BY_NAME["validate"].retries == 0
    assert STEPS_BY_NAME["emit"].retries == 0


def test_the_solver_and_everything_after_it_is_deterministic():
    """Same inputs, identical outputs — that is how a re-run is verified."""
    for name in ("select", "refine", "assemble", "emit", "validate"):
        assert STEPS_BY_NAME[name].deterministic, name
    for name in ("transcribe", "brief", "score"):
        assert not STEPS_BY_NAME[name].deterministic, name


def test_step_names_are_unique_and_ordered():
    assert len(STEP_NAMES) == len(set(STEP_NAMES))
    assert [s.name for s in STEPS] == STEP_NAMES


def test_every_step_maps_to_a_job_status_the_schema_allows():
    from mishne.db.vocab import JOB_STATUSES

    for spec in STEPS:
        assert spec.status in JOB_STATUSES, f"{spec.name} → {spec.status}"


@pytest.mark.parametrize("spec", STEPS, ids=lambda s: s.name)
def test_every_step_has_a_label_a_person_can_read(spec):
    assert spec.label and spec.label[0].isupper()
    assert spec.name.islower()


# ── the two drivers, and the fields one of them used to drop ────────────────


def test_only_one_place_constructs_an_asset_ingest():
    """`AssetIngest(...)` is called in exactly one place, and it is the shared one.

    There are two drivers over the same stages — `project.ingest` runs them
    straight through for `run.py`, and `orchestration/graph.py` runs them one at
    a time so a worker can record progress and resume. CLAUDE.md's rule is that
    where the two could drift they share the implementation instead, and this
    constructor is why the rule is written down.

    They drifted anyway. `graph.py` built its own `AssetIngest` and left out
    `asr_provider`, `width` and all three `preview_*` fields. Every field it
    omitted has a default — deliberately, so an old cache still loads — so
    nothing raised, nothing logged, and the ingest cache simply recorded
    `previewName: ""`. The preview had been rendered and uploaded; only the
    record of it was lost, and the editor showed no player for any sequence
    that went through the orchestrator, which is every sequence except the ones
    `run.py` cuts by hand.

    A test that checked `preview_name` on one driver's output would not have
    caught this — `project.ingest` was always right. What was wrong is that
    there were two constructors, so that is what is asserted.
    """
    import re

    src = Path(__file__).parent.parent / "src" / "mishne"
    # Both the bare `AssetIngest(` and the qualified `project.AssetIngest(`.
    # Excluding the qualified form is the obvious way to write this and it is
    # wrong: `project.AssetIngest(...)` is precisely the shape the drift took,
    # so a pattern that skips it passes against the very code it exists to
    # reject. `AssetIngest` in a comment or an annotation is not a call and the
    # trailing `(` is what separates them.
    calls = re.compile(r"\bAssetIngest\s*\(")
    callers = {
        path.relative_to(src).as_posix()
        for path in src.rglob("*.py")
        if calls.search(path.read_text())
    }
    assert callers == {"pipeline/project.py"}, (
        f"AssetIngest is constructed in {sorted(callers)}. Build it with "
        "`project.build_ingest` instead — a second constructor silently drops "
        "whichever defaulted fields it forgets."
    )


def test_the_shared_constructor_carries_the_preview_through():
    """The fields the drift dropped, asserted end to end on the shared path.

    `build_ingest` takes the preview from `Prepared` rather than from its own
    arguments, so a caller cannot forget to pass it — this is the test that the
    wiring inside it is right, and it fails if the preview is ever detached
    from stage 0's result again.
    """
    from mishne.pipeline import project
    from mishne.pipeline.steps.proxy import Proxy

    class _Rate:
        num, den, fps, drop_frame = 25, 1, 25.0, False

    class _Info:
        rate = _Rate()
        start_tc_frames, duration_frames = 0, 250
        width, height = 1920, 1080

    class _Asr:
        language, provider, model = "en", "xai", "grok-stt"

    class _Attribution:
        speakers: list = []

    class _Track:
        path = Path("/tmp/a0.wav")

    prepared = project.Prepared(
        info=_Info(),
        source=Path("/tmp/source.aaf"),
        preview=Proxy(path=Path("/tmp/proxy.m4a"), kind="audio", bytes=4242),
    )

    got = project.build_ingest(
        asset_id="a_1", path=Path("/tmp/source.aaf"), prepared=prepared,
        asr=_Asr(), attribution=_Attribution(), speech=object(),
        tracks=[_Track()], beats=[], warnings=[],
    )

    assert got.preview_name == "proxy.m4a"
    assert got.preview_kind == "audio"
    assert got.preview_bytes == 4242
    # The other two the orchestrator was dropping.
    assert got.asr_provider == "xai"
    assert got.width == 1920
