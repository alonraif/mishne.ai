# ADR-0018 — Two ASR engines, routed by language

**Status:** Accepted · **Date:** 2026-08-31

## Context

[ADR-0003](0003-managed-asr-behind-an-interface.md) decided managed ASR behind a
provider interface and deferred the choice of vendor to measurement. What
actually shipped through the proof of concept was the escape hatch: self-hosted
`faster-whisper`, CPU, `base` for English and `large-v3` for Hebrew. The worker
image bakes the model in.

That is roughly **one machine-hour per source hour**. It is fine for a
concierge run and for benchmarking, and it is not a service: the first customer
who uploads three hours of rushes occupies a worker for three hours, and the
answer to concurrency is more workers, each of them expensive and idle between
jobs. GPU moves the constant and not the shape — a fleet sized for peak, idle at
trough, and a capacity decision in front of every sales conversation.

The open decision recorded in the 29 Aug status — *GPU or CPU for
transcription* — was the wrong question. Both answers are a fleet.

Meanwhile, the hard requirement that used to narrow the field has stopped
narrowing it: word-level timestamps and diarization are now table stakes at the
managed vendors, at prices between $0.10 and $0.36 per source hour.

## Decision

**Managed ASR is the default; routing is by language; self-hosted Whisper stays
as a supported option and stops being the default.**

| material | engine | ~cost / source hour |
|---|---|---|
| a language xAI publishes (25 of them) | `xai/grok-stt` | $0.10 |
| Hebrew | `google/gemini-3.5-transcribe` | ~$0.30 |
| anything else, or an unidentified language | `google/gemini-3.5-transcribe` | ~$0.30 |
| a broadcaster who will not let audio leave the building | `faster-whisper` | a machine hour |

The engine list, its prices, and the language lists are **data**
(`asr/engines.json`), for the same reason the model catalog is
(ADR-0011): every identifier and price in it was verified on the day it was
written and none of them will survive the quarter.

### Why not OpenAI

`whisper-1` is the only OpenAI model that returns word-level timestamps, at
$0.36/hour. `gpt-4o-transcribe` and the mini variant are cheaper and return no
word timestamps at all, which disqualifies them here regardless of price — cuts
land between words. So the OpenAI option that meets the requirement is also more
expensive than Gemini, which meets it *and* speaks Hebrew.

### Why Hebrew is not sent to xAI

xAI's documentation says the `language` field gates text formatting rather than
transcription, and that the model handles audio in any language. If that holds,
Hebrew is three times cheaper. It is unvalidated, and the failure mode is not an
error — it is a fluent transcript of the wrong words. The engine's published
language list routes; `MISHNE_ASR_XAI_ANY_LANGUAGE=1` lifts the restriction for
a deliberate measurement run against Gemini output on the same material.

### Unidentified language routes to the wide engine

An unset language means the material has not been identified, not that it is
English. It could be Hebrew. Only an engine claiming general coverage may take
it.

## Rationale

- **It removes a fleet.** No GPU decision, no idle capacity, no per-job capacity
  planning. Concurrency becomes a rate limit rather than a purchase.
- **Cost per source hour becomes a known, small number** — between $0.10 and
  $0.30 — which is what C1 needs to price a credit and what C3 listed as
  unmeasured. Every engine call is now a `job_llm_calls` row with the audio
  duration on it (migration 0006), so the figure is a query rather than a
  project.
- **Two vendors, not one.** Neither covers the product's languages alone, and
  the split is also a failover path: an outage at one is a slower job, not a
  failed one.
- **Both engines meet the three requirements the pipeline cannot work without**
  — word timestamps, preserved disfluencies (`filler_words=true`, `verbatim`),
  and diarization.

## Consequences

**Negative, and it is the same one ADR-0003 named:** customer audio leaves the
platform, now to two vendors instead of one. Zero-retention terms are a
prerequisite for both, and this is a security decision as much as a cost one.
Gemini's path uploads the audio to the Files API, where it would otherwise sit
for 48 hours; the provider deletes it as soon as the transcript is in hand
rather than relying on the vendor's expiry.

**Gemini caps a timestamped, diarized request at 30 minutes**, so longer
material is split on silence (`asr/chunking.py`). A seam costs the model the
context of the sentence running into it, and speaker ids do not survive it —
they are namespaced per chunk rather than merged, because the diarizer never
heard the two chunks together.

**Word error rate is still unmeasured**, for these engines and for Whisper. The
selection criteria in ADR-0003 put timestamp boundary precision above WER and
nobody publishes either. The A1 corpus is what settles it; until then this is a
cost and scalability decision made on requirements that *are* checkable, and it
is reversible by one flag.

**`faster-whisper` remains in the image and in the test suite.** It is the
answer for an air-gapped customer, it is the honest baseline to measure the
managed engines against, and it is one flag away: `--asr faster-whisper`.
