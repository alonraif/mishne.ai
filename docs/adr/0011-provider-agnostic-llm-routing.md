# ADR-0011 — Any vendor's model, chosen per task by policy

**Status:** Accepted · **Date:** 2026-08-29

## Context

Three stages call a language model — the brief, span proposal, and scoring — and
each imported a vendor SDK directly. That made the vendor a property of the
pipeline rather than a deployment choice, and made the obvious economy
impossible to express: the brief parses one sentence into a duration and a
shape, and does not need the model that decides whether a span is a coherent
thought.

## Decision

One `LLMProvider` interface over four vendors — Anthropic, OpenAI, Google and
xAI — reached over plain HTTP, no SDKs. Three of the four speak the OpenAI
chat-completions shape and are one adapter.

Each task declares a floor and a preference. A **policy** (`quality`,
`balanced`, `cost`, per run or per task) picks from whatever vendors have a key.
On failure the router **falls over across vendors** and records every model that
actually ran.

**The model catalog is a data file**, not code.

Every call records cost, latency, JSON validity and constraint violations. That
evidence is reported and **does not yet change routing**.

## Rationale

- **The catalog is data because a compiled one is wrong on the day it ships.**
  Building this, I checked all four vendors' current pricing pages: not one of
  the model identifiers I would have written from memory still exists, and none
  of the prices matched. That is the normal condition for this list. An unknown
  model is therefore allowed to run, and its cost is recorded as unknown rather
  than as zero — a missing price must never read as free.
- **No vendor SDKs.** Four SDKs are four dependency trees and four ways an
  unrelated upgrade breaks a render job, in exchange for convenience worth one
  POST and one JSON body.
- **Policy chooses; measurement only watches.** Editorial quality cannot be
  measured without an editor's own EDL to compare against (ADR-0010). Cost and
  *compliance* can be measured exactly and for free: whether the answer parsed,
  and — for span proposal — how many proposals the silence gate refused, which
  is a direct corpus-free measurement of whether a model can hold to a hard
  constraint. Letting that steer routing today would demote a good model on four
  unlucky calls; recording it builds the evidence to do it properly later.
- **Failover crosses vendors.** An outage should cost a slower job, not a failed
  one. A non-retryable failure — a bad model id, a malformed request — stops the
  chain instead, because it fails identically everywhere and walking three keys
  to discover that wastes money and buries the real error.
- **`balanced` does not silently downgrade.** A task that declares it wants a
  frontier model gets one. The way to spend less is to ask for the cost policy
  and mean it.

## Consequences

- Configuration is environment only, no keys in files: `ANTHROPIC_API_KEY`,
  `OPENAI_API_KEY`, `GEMINI_API_KEY`, `XAI_API_KEY`; `--policy`;
  `MISHNE_POLICY_<TASK>`; `MISHNE_MODEL_<TASK>` to pin one outright;
  `MISHNE_MODEL_CATALOG` to replace the catalog.
- `model_versions` now lists every model per task, failover included. A job
  produced by two vendors says so.
- Measured on the 26-minute reference interview — 61 beats, 35 long enough to
  carve, 232 candidates, so 42 calls: **$0.50 at `quality`, $0.24 at `cost`.**
  Model spend is a small part of a job; transcription and compute dominate.
- `Router.estimate()` prices a job before it runs, so the credit approval in
  ADR-0006 quotes the model that will actually run rather than a guess.
- Routing is a pure function of catalog, keys and policy, so two runs with the
  same configuration choose the same model. Without that the reproducibility
  record would be meaningless.

## Open

**Promotion and demotion from measured evidence.** The data is being collected
per call. Acting on it needs a validation corpus, or the router will make
confident decisions from tiny samples — the same gap as ADR-0010, closed by the
same corpus.

**Prompts are shared across vendors.** They were written against one model's
behaviour. A prompt that works everywhere and a prompt tuned per family are
different things, and which matters is an empirical question nobody here has
answered yet.
