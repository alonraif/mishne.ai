"""Pipeline step registry — what the pipeline actually executes.

The order here is the order of the pipeline. The orchestrator's state machine is
generated from this list (`orchestration/statemachine.py`), the runner executes
it (`orchestration/graph.py`), and the progress UI renders it, so this list has
to be true or all three are wrong in the same way.

It was not true. Until B3 it omitted `speakers`, the AAF branch, span proposal
and the transcript page, and it listed `review` — a coherence pass that was
designed, never built, and would have become a phantom state in a generated
machine. `review` is now gone from the tree; ADR-0007 makes selection a
swappable stage, so adding a coherence pass later is a new stage rather than a
change to any contract, and it should be designed against real cuts rather than
guessed at now.

## The shape that matters

    ┌── per asset, cached forever ────────────────────────────────┐
    │  prepare → audio → transcribe → vad → structure → speakers  │  × N assets
    └─────────────────────────────────────────────────────────────┘
                                  ↓
    ┌── per job, across the assets it draws on ───────────────────┐
    │  brief → propose → score → select                           │
    │       → refine → assemble → emit → validate → transcript    │
    └─────────────────────────────────────────────────────────────┘

The seam is load-bearing (ADR-0008). Stages 0-4 are keyed on the asset's content
hash and survive every re-run; adding a fourth reel next month re-uses the three
already transcribed, and that is the economics of multi-upload. Everything from
`brief` onwards is cheap by comparison and is recomputed rather than cached.

`aaf_ingest` is not a stage. It is the branch `prepare` and `audio` take when
the upload is a sequence rather than a media file — ffprobe cannot read an AAF
at all — and modelling it as its own state would put a state in the machine that
most jobs skip.
"""

from .base import PAYLOAD_VERSION, Phase, StepContext, StepSpec

STEPS: list[StepSpec] = [
    # ── per asset, cached on the content hash ──────────────────────────────
    StepSpec(
        name="prepare",
        label="Probe and normalize",
        phase="asset",
        status="preparing",
        # Downloading the source is part of this step, and a download is the
        # kind of thing that fails for a moment and then works.
        retries=2,
        branches=("aaf_ingest",),
    ),
    StepSpec(
        name="audio",
        label="Extract audio",
        phase="asset",
        status="preparing",
        retries=2,
        branches=("aaf_ingest",),
    ),
    StepSpec(
        name="transcribe",
        label="Transcribe with word timestamps",
        phase="asset",
        status="transcribing",
        # The expensive one, and the one worth retrying: a managed ASR provider
        # returning 503 is not a reason to fail a job.
        retries=2,
        deterministic=False,
    ),
    StepSpec(name="vad", label="Build silence map", phase="asset", status="analyzing"),
    StepSpec(
        name="structure", label="Structure into beats", phase="asset", status="analyzing"
    ),
    StepSpec(
        name="speakers",
        label="Attribute speakers",
        phase="asset",
        status="analyzing",
        # Deterministic on multi-track audio; the single-track diarizer is a
        # model, and says so about its own confidence (ADR-0009).
        deterministic=False,
    ),
    # ── per job ────────────────────────────────────────────────────────────
    StepSpec(
        name="brief",
        label="Compile edit brief",
        phase="job",
        status="analyzing",
        # One model call, with a deterministic fallback. `llm/router.py` already
        # fails over across vendors, so one retry here is a retry of the whole
        # failover chain — more than that multiplies cost to reach the same
        # answer.
        retries=1,
        deterministic=False,
    ),
    StepSpec(
        name="propose",
        label="Propose candidate spans",
        phase="job",
        status="analyzing",
        retries=1,
        # The silence gate is deterministic; which spans are offered is not,
        # when a model is doing the proposing.
        deterministic=False,
    ),
    StepSpec(
        name="score", label="Score candidates", phase="job", status="analyzing",
        retries=1, deterministic=False,
    ),
    StepSpec(name="select", label="Solve selection", phase="job", status="selecting"),
    StepSpec(name="refine", label="Refine cut points", phase="job", status="assembling"),
    StepSpec(name="assemble", label="Assemble timeline", phase="job", status="assembling"),
    StepSpec(name="emit", label="Generate artifacts", phase="job", status="validating"),
    StepSpec(name="validate", label="Validate round-trip", phase="job", status="validating"),
    StepSpec(
        name="transcript_page",
        label="Build the transcript page",
        phase="job",
        status="validating",
    ),
]

STEPS_BY_NAME: dict[str, StepSpec] = {step.name: step for step in STEPS}

#: Steps a mode never runs, in any pass over the job.
#:
#: Not the same question as "what does *this* run skip" — a manual job pauses
#: after `brief` with `select` unrun, and runs it on the second pass once the
#: person has marked their cut (`runner.phases_for`). These are the ones no
#: pass will ever reach, and they are therefore the ones that must not be
#: planned: a `job_steps` row for a step that will never run is a progress
#: panel that stops at 85% on a job that finished, listing two stages that were
#: never going to happen.
#:
#: Manual is transcribe-and-hand-edit. Nothing proposes spans and nothing
#: scores them; the person reading the transcript is doing both.
SKIPPED_BY_MODE: dict[str, frozenset[str]] = {
    "ai": frozenset(),
    "hybrid": frozenset(),
    "manual": frozenset({"propose", "score"}),
}


def unreachable(mode: str) -> frozenset[str]:
    """The steps `mode` will never run — for planning and for display."""
    return SKIPPED_BY_MODE.get(mode, frozenset())


#: The per-asset prefix, which is what the state machine runs as a Map over the
#: job's assets, and what the ingest cache makes free on a re-run.
ASSET_STEPS = [step for step in STEPS if step.phase == "asset"]
JOB_STEPS = [step for step in STEPS if step.phase == "job"]

STEP_NAMES = [step.name for step in STEPS]


def label_for(name: str) -> str:
    spec = STEPS_BY_NAME.get(name)
    return spec.label if spec else name


__all__ = [
    "ASSET_STEPS",
    "JOB_STEPS",
    "PAYLOAD_VERSION",
    "SKIPPED_BY_MODE",
    "STEPS",
    "STEPS_BY_NAME",
    "STEP_NAMES",
    "Phase",
    "StepContext",
    "StepSpec",
    "label_for",
    "unreachable",
]
