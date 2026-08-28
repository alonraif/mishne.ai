# ADR-0002 — Durable workflow engine, not an agent framework

**Status:** Accepted · **Date:** 2026-08-28

## Context

The AI backend needs orchestration. Nous Research's **Hermes Agent** was proposed —
an MIT-licensed, model-agnostic open-source agent with persistent memory, autonomous
skill creation, parallel subagents, sandboxed execution backends, a cron scheduler,
and gateways to Telegram, Discord, Slack, WhatsApp and Signal.

It is genuinely well-built. The question is whether it is the right shape for this
workload.

## Decision

**Orchestrate the edit pipeline with a durable workflow engine — AWS Step Functions —
not an agent framework.** LLM calls are individual steps inside that workflow, each
with a pinned prompt version, a pinned model, and a structured output schema.

Hermes Agent is not adopted for the pipeline.

## Rationale

The edit pipeline is a known DAG that runs unattended for 10–60 minutes and must be
reproducible, retriable, and billable at a predictable cost. That is a workflow, not
an agent loop.

Specifically, against Hermes Agent for this use:

1. **Persistent cross-session memory is a liability here, not a feature.** This is
   multi-tenant, and the content is embargoed broadcast material. An orchestrator
   that accumulates memory across jobs creates a data-isolation problem to be fought
   rather than a capability to be used.

2. **Autonomous skill creation is incompatible with reproducibility.** When a
   broadcaster asks why their best soundbite was dropped, the answer must be a pinned
   prompt version, a pinned model version, and a stored rationale. A system that
   rewrote its own skill last week cannot answer that, and "the AI improved itself"
   is not an acceptable response to a professional editor.

3. **Wrong failure semantics.** A 40-minute media job needs durable execution,
   per-step retry with backoff, idempotency, and resumption from the failed step. An
   agent loop provides none of these.

4. **Unbounded cost and latency.** Agent loops have unbounded step counts. mishne.ai
   sells a job with a price and an expected completion time.

5. **Most of it is dead weight.** Messaging gateways, text-to-speech, image
   generation, browser automation — none apply.

The general principle: **use an agent where the task is open-ended and a human is in
the loop; use a workflow engine where the task is a known DAG and the output must be
reproducible.**

## Where Hermes Agent does fit

Not rejected outright — rejected for this role:

- **Internal operations tooling.** Job triage, log analysis, support workflows. This
  is close to what LogHawk already does internally and Hermes is a reasonable fit.
- **The v2 interactive refinement surface.** *Make it two minutes shorter, keep the
  merger part.* Open-ended, human in the loop, bounded blast radius because it
  operates on an existing selection. That is a legitimate agentic UX, and it sits on
  top of this pipeline rather than replacing it.

## Why Step Functions rather than Temporal

Temporal is the more capable engine and the likely eventual destination. It is the
wrong starting point for a small team: operational and learning cost paid before
there is a product.

Step Functions is native to AWS, has nothing to operate, and provides durable
execution, declarative retries, and per-execution history out of the box.

**The migration is kept cheap by design:** every step is a pure, idempotent function
of `(job_id, step_input_ref) → step_output_ref`, with payloads in S3 and status in
Postgres. Switching orchestrators rewrites the orchestration layer only. Migrate when
workflow logic outgrows what ASL expresses comfortably — dynamic fan-out over
variable source-clip counts is the likely trigger.

## Consequences

**Positive** — reproducible, auditable, predictable cost and latency; no agent
framework in the critical path; orchestrator swappable.

**Negative** — less flexible than an agent; adding a pipeline stage means editing a
state machine definition. This is the correct trade for a production pipeline.

**Constraint accepted** — Step Functions has a 256 KB state payload limit. Steps pass
references, never payloads. This is enforced by the step contract above.
