# ADR-0009 — Diarize per source region, on ONNX, and say when it is unsure

**Status:** Accepted · **Date:** 2026-08-29

## Context

Multi-track material needs no model to answer "who is speaking": each person has
a microphone, the loudest track wins, and the answer is arithmetic. Single-track
material has none of that, and the pipeline's answer was one speaker with
`reliable: true`. On the first real Hebrew footage — a presenter interviewing two
designers — that put a three-way conversation in one mouth and asserted it was
trustworthy. The transcript page renders that as fact beside every line.

## Decision

Diarization goes behind a provider Protocol, alongside ASR (ADR-0003). The local
provider runs **pyannote segmentation and a WeSpeaker embedding model as ONNX**
via sherpa-onnx.

Material is diarized **per source region** — the clips of a sequence — and the
per-region results are matched afterwards by clustering the turn embeddings.

Where separation is weak, the result says so, and where there is no diarizer at
all the answer is "voices were never separated", never a speaker.

## Rationale

- **ONNX, not torch.** Torch is a two-gigabyte dependency and the pyannote
  pipeline is gated behind a Hugging Face account and a licence acceptance,
  neither of which belongs in a customer's install path. ONNX Runtime is already
  a dependency for the VAD; the two models are 36 MB against Whisper's 3 GB.
- **Per region, because embeddings encode the microphone.** Measured on the
  reference material: diarizing the assembled audio whole returned five speakers
  whose boundaries sat on the clip seams — it had partitioned the audio by
  camera. The same audio, one clip in isolation, returned three speakers
  matching the conversation by ear.
- **Raw embeddings when matching across regions, not channel-compensated.**
  Subtracting each region's mean embedding is the textbook channel correction
  and it is wrong here: it assumes regions contain a mix of speakers. In
  narration most regions are one person, so the region mean *is* that person and
  subtracting it removes the signal. Measured: 14 speakers, none above 17% of
  the audio, against 3 for the uncompensated version.
- **Separation is not identification.** The output is "three distinct voices",
  never who they are. Naming stays a human act and `confirmed` stays false until
  a person does it.
- **An honest "unsure" beats a confident guess.** A wrong speaker label is
  invisible in a delivered cut — nobody reviewing the AAF can tell — so the
  failure has to surface at the point of use.

## Consequences

- `--diarize <dir>` is opt-in. Without it, single-track material returns no
  speakers and the legend has nothing to show, which is the truthful state.
- `MERGE_DISTANCE` (0.45) was swept against the reference material: 0.35 splits
  one presenter in two, 0.65 merges a distinct voice away. It is the one knob.
- Quality on short utterances is poor and admitted. A four-second answer against
  three minutes of narration does not embed confidently, and the result is
  flagged unreliable rather than smoothed over.
- **There is no ground truth yet.** Tuning was against one segment judged by ear.
  A labelled corpus is what would turn this from defensible into measured, and
  it is the same gap Spike B has for selection.

## Alternatives considered

**pyannote.audio on torch.** Better models, and the gate plus the dependency
weight make it the wrong default. The Protocol exists so it can be added as a
provider for customers who want it.

**Diarize the assembled sequence whole.** Rejected on measurement above.

**Report one speaker as before.** Rejected: it is not a simplification, it is a
false statement rendered as fact.
