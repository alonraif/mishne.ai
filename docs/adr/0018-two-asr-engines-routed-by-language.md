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
| Hebrew | `google/gemini-3.5-transcribe` | ~$0.18 (measured; $0.30 published) |
| anything else, or an unidentified language | `google/gemini-3.5-transcribe` | ~$0.18 |
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
transcription, and that the model handles audio in any language. If that held,
Hebrew would be roughly half the price. **It was measured on 1 September 2026
and it does not hold** — see *Measured, 1 September 2026* below. The engine's
published language list routes; `MISHNE_ASR_XAI_ANY_LANGUAGE=1` lifts the
restriction, and is now the harness for re-taking that measurement when the
model changes rather than a saving waiting to be switched on.

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

## Measured, 31 August 2026

Both engines on the same 25.7 minutes of unedited English rushes, and Gemini on
3.7 minutes of Hebrew.

| | xAI grok-stt | Gemini 3.5 Transcribe | faster-whisper large-v3 |
|---|---|---|---|
| words | 4,743 | 4,737 | **4,542** |
| speakers found | 2 | 2 | **0** |
| filler retained | 4.3% | 4.2% | **3.2%** |
| wall clock, 25.7 min of audio | 17.9s (86x real time) | 71.6s (22x) | **3,702s (0.42x)** |
| cost | $0.0429 | $0.0772 | 61.7 machine-minutes |
| per source hour | $0.100 | $0.180 | **2.4 machine-hours** |

### The boundaries, triangulated

Median start-time difference on words each pair both returned:

| | vs xAI | vs Gemini |
|---|---|---|
| **Gemini** | **30 ms** (4,451 words, 94%) | — |
| **faster-whisper** | 170 ms (4,016, 88%) | 180 ms (4,125, 87%) |

One frame at 25 fps is 40 ms. The two managed engines put the same word inside
the same frame as each other; the self-hosted model is four to five frames away
from **both** of them.

Agreement is not accuracy, and none of this is ground truth. But two
independently built engines agreeing to 30 ms while a third differs from each of
them by ~175 ms identifies which one is the outlier, and there is no reading
where the odd one out is the pair. ADR-0003 says a provider at 3% WER with
sloppy word boundaries produces worse cuts than one at 5% with tight ones, that
nobody publishes this, and that it has to be measured. It is measured, and the
loose boundaries belong to the model being replaced.

For a language both managed engines cover, the choice between them is therefore
**purely a cost decision** — which is what the routing already assumes.

### The other three columns

**Both managed engines honour verbatim; Whisper does not.** 4.3% and 4.2% filler
against 3.2%, and 200 fewer words overall — Whisper returns 4.2% less text and
proportionally less of the filler inside it. `faster_whisper_provider.py` warned
that Whisper "will still tidy some speech — it is trained on written-style
targets" and called it a real limitation to measure rather than assume away.
This is that measurement: it tidies, and tidying is the one thing this system
cannot have an ASR do, because removing filler is its own job (`asr/base.py`).

**Whisper returns no speakers**, as its provider says it will. The self-hosted
path therefore also needs the separate ONNX diarizer and its weights (ADR-0009);
the managed engines include diarization in the price above.

**Self-hosting is not cheaper, it is only unpriced.** 2.4 machine-hours per
source hour is the number to multiply by whatever a CPU hour costs. There is no
plausible instance price at which that lands under $0.10, and it buys the looser
boundaries and the tidied transcript above. GPU moves the constant and not the
argument.

**Gemini's audio tokenisation is exactly as published**: 25.0 tokens per second
across both runs, against a documented 25.

**Word error rate is still unmeasured**, for these engines and for Whisper. The
selection criteria in ADR-0003 put timestamp boundary precision above WER and
nobody publishes either. The A1 corpus is what settles it; until then this is a
cost and scalability decision made on requirements that *are* checkable, and it
is reversible by one flag.

**`faster-whisper` remains in the image and in the test suite.** It is the
answer for an air-gapped customer and it is one flag away: `--asr
faster-whisper`. What the measurement above changes is what to tell that
customer — on-premise costs 2.4 machine-hours per source hour, loses speaker
attribution unless the diarizer is deployed too, and places cut points four to
five frames from where the managed engines agree they are. That is a real
product, and it is a worse one; a broadcaster choosing it should choose it
knowing that, rather than being sold it as equivalent.

## Measured, 1 September 2026 — xAI on Hebrew

The run the flag exists for. Both engines on the same 3.7 minutes of Hebrew
(`SyncDaniel.aaf`), xAI reached via `MISHNE_ASR_XAI_ANY_LANGUAGE=1`.

| | xAI grok-stt | Gemini 3.5 Transcribe |
|---|---|---|
| words | 421 | 478 |
| **speakers found** | **1** | **3** |
| filler retained | 0.0% | 0.4% |
| wall clock, 3.7 min of audio | 2.9s (77x real time) | 14.9s (15x) |
| cost | $0.0062 | $0.0111 |
| per source hour | $0.100 | $0.180 |
| language reported back | **`en`** | `he` |

**It transcribes Hebrew, and it transcribes it wrong.** The two transcripts
share 37% of Gemini's vocabulary. Where Gemini has `מעצבות הפנים של הפרויקט`
("the project's interior designers") xAI has `אצופות את פנימי של הפרויקט`, which
is not Hebrew; where Gemini has `בית המסיבות האולטימטיבי` ("the ultimate party
house") xAI has `בת-מסיבות אולימפיות` ("Olympic party daughter"). Twelve tokens
carry a script the speaker never used — Russian `там`, Arabic `بين`, romanised
Hebrew `ב-etsem` — four of them mixed *inside* a single Hebrew word
(`אlements`, `בבеж`). `asr/script.py` repairs Arabic letters inside Hebrew
words; it cannot repair a word that was never heard.

This is precisely the failure `Engine.speaks` is written to prevent: not an
error, not a low score, but a fluent transcript of the wrong words. An editor
reading it in a rough cut has no way to tell.

**Diarization is the second disqualification and it is on its own sufficient.**
All 421 words came back on one speaker. Stage 4 needs a speaker change to find a
beat boundary and stage 7 needs `speaker_priority`; a single-speaker transcript
of a three-speaker interview is not a cheaper input, it is a different job.

**And nothing in the response says any of this.** `language` comes back as `en`
whether `he` is sent or omitted. A control run with the field omitted entirely
returned 417 words, one speaker, and a word-for-word identical opening — so the
documentation's claim about the field is true (it is formatting-only) and the
claim about the model is false. There is no vendor signal to route on, no error
to fail over from, and the ledger would record a successful $0.0062 call.

**The 34 ms boundary agreement is not a mitigation.** It held over only 45% of
words, against 94% on English. Two engines placing the same word in the same
frame means nothing when they only agree on the word 45% of the time.

The saving forgone is $0.08 per source hour. The routing stands.
