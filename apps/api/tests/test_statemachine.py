"""The generated state machine, checked against the registry it is generated from.

Not a test of Step Functions — of the two things that would silently break a
deploy: a state the worker cannot run, and a stage with no state at all.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from mishne.orchestration import statemachine  # noqa: E402
from mishne.pipeline.steps import ASSET_STEPS, JOB_STEPS, STEPS_BY_NAME  # noqa: E402


def _definition() -> dict:
    return statemachine.build("arn:aws:states:::ecs:runTask.sync")


def test_every_stage_has_a_state_and_every_state_a_stage():
    d = _definition()
    top = set(d["States"]) - {"Complete", statemachine.FAILURE_STATE, "Failed", "Ingest"}
    inner = set(d["States"]["Ingest"]["Iterator"]["States"])

    assert inner == {s.name for s in ASSET_STEPS}
    assert top == {s.name for s in JOB_STEPS}
    assert not (top | inner) - set(STEPS_BY_NAME)


def test_the_assets_are_a_map_because_a_job_has_however_many_it_has():
    ingest = _definition()["States"]["Ingest"]
    assert ingest["Type"] == "Map"
    assert ingest["ItemsPath"] == "$.assets"
    # Each branch is a worker holding a whole asset on local disk (ADR-0013), so
    # unbounded fan-out is unbounded disk.
    assert 1 <= ingest["MaxConcurrency"] <= 8


def test_the_machine_carries_ids_and_never_a_payload():
    # Step Functions caps its state at 256 KB and a transcript is larger than
    # that on a short interview.
    text = json.dumps(_definition())
    for forbidden in ("transcript", "beats", "words", "brief_text"):
        assert f'"{forbidden}"' not in text
    task = _definition()["States"]["brief"]
    assert set(task["Parameters"]) <= {"job_id.$", "org_id.$", "step", "asset_id.$"}


def test_retries_come_from_the_registry():
    d = _definition()
    assert d["States"]["brief"]["Retry"][0]["MaxAttempts"] == STEPS_BY_NAME["brief"].retries
    # Not "no policy" but "not retryable": writing the same wrong artifact twice
    # costs money and changes nothing.
    assert "Retry" not in d["States"]["validate"]
    assert "Retry" not in d["States"]["assemble"]


def test_every_state_catches_into_the_release_path():
    """A job that dies without releasing its hold is a wrong balance (ADR-0006)."""
    d = _definition()
    for name, state in d["States"].items():
        if state.get("Type") != "Task" or name in ("Complete", statemachine.FAILURE_STATE):
            continue
        assert state["Catch"][0]["Next"] == statemachine.FAILURE_STATE, name
    assert d["States"]["Ingest"]["Catch"][0]["Next"] == statemachine.FAILURE_STATE
    assert d["States"][statemachine.FAILURE_STATE]["Parameters"]["step"] == "__fail__"


def test_the_chain_is_the_pipeline_order():
    d = _definition()
    assert d["StartAt"] == "Ingest"
    assert d["States"]["Ingest"]["Next"] == JOB_STEPS[0].name
    for a, b in zip(JOB_STEPS, JOB_STEPS[1:]):
        assert d["States"][a.name]["Next"] == b.name
    assert d["States"][JOB_STEPS[-1].name]["Next"] == "Complete"

    inner = d["States"]["Ingest"]["Iterator"]
    assert inner["StartAt"] == ASSET_STEPS[0].name
    for a, b in zip(ASSET_STEPS, ASSET_STEPS[1:]):
        assert inner["States"][a.name]["Next"] == b.name
    assert inner["States"][ASSET_STEPS[-1].name]["End"] is True


def test_it_is_valid_json_and_prints():
    assert json.loads(json.dumps(_definition()))
    assert statemachine.main(["arn:test"]) == 0


def test_the_checked_in_definition_is_what_this_code_generates():
    """`infra/statemachine.json` is generated, and drift is a deploy that lies.

    Regenerate it with:

        python -m mishne.orchestration.statemachine > infra/statemachine.json
    """
    import json as _json

    checked_in = Path(__file__).resolve().parents[3] / "infra" / "statemachine.json"
    assert checked_in.exists(), "run the generator; see the docstring"
    assert _json.loads(checked_in.read_text()) == statemachine.build()


def test_the_generator_needs_none_of_the_media_stack():
    """Importable on a machine with no OpenTimelineIO, ffmpeg or solver.

    Infra tooling generates the machine from the registry, and the API process
    plans a job's steps; neither has any business needing the pipeline's
    dependencies, and `orchestration/__init__` is lazy so that they do not.
    """
    import subprocess
    import sys as _sys

    src = Path(__file__).resolve().parents[1] / "src"
    result = subprocess.run(
        [
            _sys.executable, "-c",
            "import sys; sys.modules['opentimelineio'] = None; "
            "from mishne.orchestration import statemachine; "
            "assert statemachine.build()['StartAt'] == 'Ingest'; print('ok')",
        ],
        capture_output=True, text=True, env={"PYTHONPATH": str(src), "PATH": "/usr/bin:/bin"},
    )
    assert result.returncode == 0, result.stderr[-2000:]
