"""The Step Functions definition, generated from the step registry.

Generated rather than written, because a hand-maintained state machine and a
hand-maintained registry are two descriptions of the same pipeline that diverge
the first week — which is exactly what B3 found when it started: a registry
listing a stage that did not exist and omitting four that did.

    python -m mishne.orchestration.statemachine > infra/statemachine.json

## The shape

    ┌ Ingest (Map over the job's assets, one branch each) ┐
    │   prepare → audio → transcribe → vad → structure    │
    │            → speakers                               │
    └─────────────────────────────────────────────────────┘
                            ↓
      brief → propose → score → select → refine → assemble
            → emit → validate → transcript_page → Succeed

A `Map` rather than a chain of per-asset states: a job draws on however many
uploads it draws on, and the machine's shape cannot depend on that number.
`MaxConcurrency` is bounded because each branch is a worker holding the whole
asset on local disk (ADR-0013), so unbounded fan-out is unbounded disk.

## What each state does

Every state is a `Task` that hands the worker `{job_id, asset_id, step}` and
nothing else. **Not the payload.** Step Functions caps its state at 256 KB and a
transcript is bigger than that on a short interview, so what moves through the
machine is identifiers, and what an identifier names lives in object storage.

## Retries

Per stage, from the registry, and deliberately asymmetric. Transcription and the
model stages retry, because a provider returning 503 is not a reason to fail
somebody's job. Assembly and validation do not: a validation failure means an
artifact is wrong, and writing it again produces the same wrong artifact.

Every state catches into one failure handler, which is what releases the credit
hold. A job that dies without releasing its hold is a customer whose balance is
wrong until somebody notices.
"""

from __future__ import annotations

import json
import sys

from ..pipeline.steps import ASSET_STEPS, JOB_STEPS, StepSpec

#: How many assets are ingested at once. Each branch is a worker with the whole
#: asset on disk plus its extracted audio, so this multiplies the disk budget in
#: ADR-0013 rather than being free parallelism.
MAX_ASSET_CONCURRENCY = 4

#: The failure path every state catches into. It marks the job failed and
#: releases the hold; ADR-0006 is why that is not optional.
FAILURE_STATE = "ReleaseAndFail"


def _retry(spec: StepSpec) -> list[dict]:
    if not spec.retries:
        # Not "no retry policy" but "this is not retryable", and the difference
        # matters to whoever reads the generated file.
        return []
    return [
        {
            "ErrorEquals": ["States.TaskFailed", "States.Timeout"],
            "IntervalSeconds": 2,
            "MaxAttempts": spec.retries,
            "BackoffRate": 3.0,
        }
    ]


def _task(spec: StepSpec, worker_arn: str, next_state: str | None, *, in_map: bool) -> dict:
    state: dict = {
        "Type": "Task",
        "Resource": worker_arn,
        "Comment": spec.label,
        "Parameters": {
            "job_id.$": "$.job_id",
            "org_id.$": "$.org_id",
            "step": spec.name,
            **({"asset_id.$": "$.asset_id"} if in_map else {}),
        },
        # The worker writes progress and artifacts; the machine carries ids.
        "ResultPath": None,
    }
    if retries := _retry(spec):
        state["Retry"] = retries
    state["Catch"] = [
        {
            "ErrorEquals": ["States.ALL"],
            "Next": FAILURE_STATE,
            "ResultPath": "$.error",
        }
    ]
    if next_state:
        state["Next"] = next_state
    else:
        state["End"] = True
    return state


def _chain(specs: list[StepSpec], worker_arn: str, *, in_map: bool,
           final_next: str | None) -> dict[str, dict]:
    states: dict[str, dict] = {}
    for i, spec in enumerate(specs):
        following = specs[i + 1].name if i + 1 < len(specs) else final_next
        states[spec.name] = _task(spec, worker_arn, following, in_map=in_map)
    return states


def build(worker_arn: str = "${worker_task_arn}") -> dict:
    """The whole definition. `worker_arn` is left as a Terraform placeholder."""
    asset_states = _chain(ASSET_STEPS, worker_arn, in_map=True, final_next=None)
    job_states = _chain(JOB_STEPS, worker_arn, in_map=False, final_next="Complete")

    return {
        "Comment": (
            "mishne.ai — raw footage to an editable rough cut. Generated from "
            "mishne.pipeline.steps.STEPS; do not edit by hand."
        ),
        "StartAt": "Ingest",
        "States": {
            "Ingest": {
                "Type": "Map",
                "Comment": (
                    "Stages 0-4 per upload, cached on the asset's content and "
                    "never repaid (ADR-0008)."
                ),
                "ItemsPath": "$.assets",
                "MaxConcurrency": MAX_ASSET_CONCURRENCY,
                "Parameters": {
                    "job_id.$": "$.job_id",
                    "org_id.$": "$.org_id",
                    "asset_id.$": "$$.Map.Item.Value",
                },
                "Iterator": {
                    "StartAt": ASSET_STEPS[0].name,
                    "States": asset_states,
                },
                "ResultPath": None,
                "Catch": [
                    {"ErrorEquals": ["States.ALL"], "Next": FAILURE_STATE,
                     "ResultPath": "$.error"}
                ],
                "Next": JOB_STEPS[0].name,
            },
            **job_states,
            "Complete": {
                "Type": "Task",
                "Resource": worker_arn,
                "Comment": "Settle the hold at min(actual, approved cap) (ADR-0006).",
                "Parameters": {
                    "job_id.$": "$.job_id",
                    "org_id.$": "$.org_id",
                    "step": "__complete__",
                },
                "End": True,
            },
            FAILURE_STATE: {
                "Type": "Task",
                "Resource": worker_arn,
                "Comment": "Release the whole hold. A failed job is never charged.",
                "Parameters": {
                    "job_id.$": "$.job_id",
                    "org_id.$": "$.org_id",
                    "step": "__fail__",
                    "error.$": "$.error",
                },
                "Next": "Failed",
            },
            "Failed": {
                "Type": "Fail",
                "Error": "JobFailed",
                "Cause": "A stage failed after its retries; the hold was released.",
            },
        },
    }


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    arn = argv[0] if argv else "${worker_task_arn}"
    print(json.dumps(build(arn), indent=2))
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
