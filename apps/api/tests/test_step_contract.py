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
