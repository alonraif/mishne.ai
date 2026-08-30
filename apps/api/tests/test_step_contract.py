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
        "prepare", "audio", "transcribe", "vad", "structure", "speakers",
    ]
    assert JOB_STEPS[0].name == "brief"
    assert JOB_STEPS[-1].name == "transcript_page"


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
