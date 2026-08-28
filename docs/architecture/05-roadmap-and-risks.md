# 05 — Roadmap, Risks & Cost

## Phase 0 — De-risk before building anything

**Two weeks. No UI, no auth, no database. Two CLI spikes.**

Both spikes test assumptions that, if wrong, invalidate large parts of the plan. Both
are cheap. Doing them first is the highest-leverage decision available right now.

### Spike A — The AAF spike

*Does the interchange layer actually work?*

Hand-build an OTIO timeline with ~20 cuts against one real source. Export AAF,
FCPXML, and EDL. Open each in Media Composer, Premiere, Resolve, and Final Cut.

Verify against the acceptance table in
[02 — Media & Interchange](02-media-and-interchange.md): file opens, media relinks,
cut count matches, first and last frame timecode exact, audio in sync at the
three-hour mark, handles present and trimmable. Test at 23.976, 25, 29.97 DF and
29.97 NDF.

**This is the highest technical risk in the entire project and it has nothing to do
with AI.** The generic AAF writers have a documented history of producing files Media
Composer chokes on. If they fail, the fallback is writing AAF with `pyaaf2` directly
against a known-good reference AAF exported from Media Composer itself — a real
chunk of work that must be discovered now, not in month three.

Success criterion: a hand-built 20-cut timeline round-trips cleanly into all four
NLEs at all four frame rates.

> **Built.** `spikes/aaf-roundtrip/` — see its
> [README](../../spikes/aaf-roundtrip/README.md) for findings. Automated checks
> pass for all four formats at all four rates. The NLE half is still open and is
> the part that actually settles this risk.
>
> Two findings already change the plan. AAF writes reliably **once clips carry
> an explicit MobID** — the MobID is the relink key, and the writer's
> `use_empty_mob_ids` option produces files that cannot be relinked. And
> **FCPXML is the fragile one**, not AAF: its adapter cannot write NTSC rates
> without a patch, and reads them back ~4% wrong. That inverts the risk ordering
> assumed in [02 — Media & Interchange](02-media-and-interchange.md).

### Spike B — The quality spike

*Is text-only selection good enough to be worth paying for?*

Take two or three pieces where both the raw material and the finished cut exist —
ideally from LiveU customers or from material you can obtain. Run transcription,
structure into beats, and produce a selection. Compare against what the human editor
actually used.

**Define the metric now, because it is the product's north star:** temporal overlap
between AI-selected ranges and human-selected ranges, as a fraction of the human cut.

Rough interpretation:

| Overlap | Reading |
|---|---|
| > 60% | Strong. The concept works; the rest is engineering |
| 40–60% | Viable. Editors save real time even while disagreeing with choices |
| < 40% | The premise needs rework before any product is built |

These thresholds are provisional. Two editors given the same rushes do not agree
either, so human-to-human agreement is the real ceiling and it is unknown. Until
it is measured, **lift over the best baseline is the trustworthy signal**, not
the absolute score.

Measure recall of the human's selections more than precision — a rough cut that is
somewhat long but contains everything the editor wanted is useful; one that is
exactly the right length but missing the best soundbite is not. Weight accordingly.

**Do not skip Spike B because it is less fun than building.** It is the one that
determines whether the product is worth building at all.

> **Built.** `spikes/selection-quality/` — see its
> [README](../../spikes/selection-quality/README.md). The harness, the metric
> and four baselines are done and running. It needs real pairs.
>
> Two things already change the plan. **Ground truth is free**: the editor's own
> finished sequence, exported as an EDL or AAF, is an exact record of what they
> used — no annotation, and one customer export per corpus entry. And
> **`longest` is a far stronger baseline than expected**, beating the non-LLM
> control on the fixture. The question is therefore not whether the engine beats
> random; it is whether the language model beats "pick the longest answers" by
> enough to justify its cost.

## Phase 1 — Vertical slice

**3–4 weeks.** One user, one project, hardcoded. MP4 in, FCPXML + EDL + transcript
JSON out. Real pipeline end to end, no auth beyond a shared secret, single tenant,
CLI or the crudest possible web form.

FCPXML before AAF — it is the forgiving format, and the goal here is to prove the
pipeline, not the hardest interchange target.

Deliberately absent: orgs, roles, resumable upload, progress UI, retries, cost
tracking.

Success criterion: a real three-hour source produces a rough cut an editor is willing
to open and comment on.

## Phase 2 — Product

**6–8 weeks.** The shippable MVP.

- WorkOS auth, orgs, roles
- Projects UI, resumable upload, job submission with guided brief
- Step Functions orchestration with retries
- Job progress over SSE
- Transcript page with used/unused and rationale
- AAF output plus the validation gate
- Artifact downloads

Success criterion: a customer runs a job unaided and uses the result in a real edit.

## Phase 3 — Hardening

**4 weeks, overlapping Phase 2.** Should not be sequenced after — several items are
far cheaper built in than retrofitted.

- Postgres RLS and cross-tenant tests in CI
- KMS per-org keys, bucket policies, presign scoping
- Retention policy and hard delete
- Audit log
- Content-free logging enforced and tested
- Vendor zero-retention agreements executed
- Per-job cost telemetry
- OpenTelemetry traces, alarms, dashboards

## Phase 4 — Depth

Ordered by expected value:

1. **Audio-only ingest path** and a desktop helper that extracts locally. Removes the
   worst part of the experience and most of the storage cost and security exposure.
   Arguably belongs in Phase 2 for the professional segment.
2. **AAF sequence input** with multicam and multi-track handling.
3. **Interactive refinement** — *make it two minutes shorter, keep the merger part.*
   The legitimate agentic surface, built on re-solving Stage 7 with modified
   constraints.
4. **Lightweight visual quality pass** — see risk 2 below.
5. Team collaboration on the transcript page.

---

## The riskiest assumptions

Named explicitly, because unnamed assumptions are the ones that break projects.

### 1. AAF into Avid works reliably

Highest technical risk. Fully testable in week one. Fallback exists but is expensive.
**Mitigation: Spike A, before anything else.**

### 2. Text-only selection is sufficient

The premise is that the AI never needs to see pixels. This is largely true and it is
what makes the economics work — but it has a real limit.

A perfect soundbite delivered while the subject looks away, or while the shot is soft,
or while someone walks through frame, is unusable. The transcript cannot know this.
An editor receiving a rough cut with three unusable selects will conclude the tool
does not understand video.

Mitigation, and it is cheap: shot-change detection with PySceneDetect plus a small
vision model call on sampled frames at each candidate cut point, scoring only basic
technical quality — is the subject in frame, is it in focus, is there an obvious
obstruction. A few frames per candidate, not continuous video analysis. This preserves
the core principle while closing the gap. **Expect to need this sooner than planned.**

### 3. Director's notes are specific enough to act on

In practice they are often "make it punchy, about ten minutes." Mitigation is the
guided form plus surfaced `clarifications` described in
[01 — Edit Engine](01-edit-engine.md). Watch for this in Spike B: if selection quality
correlates strongly with note quality, the brief input is a product problem, not a
model problem.

### 4. Multilingual ASR quality, particularly Hebrew

Published benchmarks cover English well and Hebrew essentially not at all. Word error
rate is also the wrong metric — **timestamp boundary precision matters more**, and no
vendor publishes it.

Mitigation: benchmark two or three providers on real Hebrew and English material
during Phase 0, scoring boundary precision directly. Do not commit to a language in
marketing before it has been measured.

### 5. Upload time dominates the experience

A 200 GB ProRes master over a typical connection is a four-to-six hour upload. No
amount of pipeline speed compensates. Mitigation is the audio-only path — see
[ADR-0005](../adr/0005-audio-only-ingest-path.md) — and honest progress reporting
until it ships.

### 6. Vendor concentration

The pipeline depends on external ASR and LLM providers for its core function. Pricing
changes, deprecations, and outages are all outside your control. Mitigation is the
provider interface, plus keeping raw ASR responses so reprocessing never requires
re-transcription.

---

## Cost model

Per three-hour job. Figures are order-of-magnitude, for sanity-checking pricing.

### Variable cost

| Item | Cost | Notes |
|---|---|---|
| Audio extraction | $0.05–0.15 | I/O-bound; reading 200 GB from S3 dominates. Near zero on the audio-only path |
| ASR | $0.30–0.75 | 3 h at $0.10–0.25/h batch pricing |
| VAD, structuring, solver | < $0.01 | CPU, seconds |
| LLM — scoring | $0.70–3.40 | ~65k input, ~32k output tokens. Bulk of LLM cost |
| LLM — brief + review | $0.10–0.45 | Small token counts, worth a stronger model |
| Artifact generation | < $0.01 | |
| **Total compute + vendors** | **~$1.15–5.00** | |

Use a cheaper model for scoring — the bulk of tokens, a well-structured task — and a
stronger one for brief compilation and sequence review, where judgment matters and
volume is low. That split is roughly the difference between the low and high ends
above.

### Storage — the dominant lever

| Ingest mode | Size, 3 h | 30-day storage cost |
|---|---|---|
| ProRes 422 HQ | ~297 GB | ~$6.83 |
| ProRes 422 | ~198 GB | ~$4.56 |
| DNxHD 145 | ~196 GB | ~$4.50 |
| H.264 25 Mbit/s | ~34 GB | ~$0.78 |
| **Audio only, 16 kHz mono** | **~0.35 GB** | **~$0.01** |

At S3 Standard pricing of roughly $0.023/GB-month. Transfer in is free; artifact
downloads are megabytes and negligible.

**Storage of full mezzanine media costs more per job than the entire AI pipeline.**
That is the single most important number in this table. It makes retention policy a
pricing decision, and it makes the audio-only path an economic argument as much as a
security or experience one.

### Implication

Variable cost lands somewhere between **~$1 per job** (audio-only ingest, efficient
model mix) and **~$10 per job** (ProRes ingest, 30-day retention, strong model
throughout).

Against a plausible price point — whether per-job in the tens of dollars or a seat
plus usage model — margin is comfortable in every configuration. The risk is not that
unit economics fail; it is that they drift unnoticed. Hence per-job cost telemetry in
Phase 3: a prompt change that triples token count should show up on a dashboard, not
in a quarterly bill.

---

## What to do next

1. **Spike A and Spike B, in parallel, starting now.** Everything below is contingent
   on both.
2. Benchmark ASR providers on real English and Hebrew material, scoring timestamp
   boundary precision, not word error rate.
3. Decide the ingest default per segment — creators to full media, professionals to
   audio-only.
4. Only then start Phase 1.
