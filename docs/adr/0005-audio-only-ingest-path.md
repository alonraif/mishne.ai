# ADR-0005 — Audio-only ingest as a first-class path

**Status:** Accepted · **Date:** 2026-08-28

## Context

The output of mishne.ai is an edit decision — a timeline referencing the customer's
own media by timecode. It contains no pixels. The pipeline itself needs audio and
metadata; video is never decoded beyond probing.

Yet the obvious product design asks the customer to upload the video.

## Decision

Support **audio-only ingest as a first-class path**, alongside full-media and AAF
ingest. Default professional users to audio-only; default creators to full media.

The customer supplies a mixdown or per-track audio plus source timecode metadata,
either by exporting from their NLE or via a desktop helper that extracts locally.

## Rationale

For a three-hour job the engine needs roughly 350 MB of 16 kHz mono audio. A ProRes
422 master of the same material is around 200 GB — a factor of roughly 500.

| Source | Size, 3 h | Upload at 100 Mbit/s | 30-day storage |
|---|---|---|---|
| ProRes 422 | ~200 GB | ~4.5 h | ~$4.60 |
| H.264 25 Mbit/s | ~34 GB | ~45 min | ~$0.78 |
| **16 kHz mono WAV** | **~0.35 GB** | **~30 s** | **~$0.01** |

Three separate wins, each independently sufficient to justify the path:

1. **Experience.** Upload goes from the worst part of the product to a non-event. No
   pipeline optimization compensates for a four-hour upload.
2. **Cost.** Storing full mezzanine media costs more per job than the entire AI
   pipeline. This removes the dominant cost line.
3. **Security, and this is the strongest.** mishne.ai never holds the customer's
   footage. For a broadcaster with embargoed material, that is not a feature — it is
   frequently the difference between a possible deal and an impossible one.

## Consequences

**Positive** — dramatically faster onboarding for professional users; storage cost
approaches zero; the security conversation becomes far easier; smaller blast radius
in any incident.

**Negative, and stated plainly**

- Timecode alignment cannot be independently verified. If the customer supplies wrong
  TC, the output is wrong and it will not be obvious why. Mitigate with a validation
  step comparing declared duration against actual audio duration, and clear
  documentation of the export procedure per NLE.
- The relink burden shifts to the customer, who must have the media locally.
- No path to a future in-browser review player for these jobs without a separate
  proxy upload.
- Two ingest paths to build, test, and support.

**Not a replacement.** Full-media and AAF ingest remain, and AAF-with-embedded-essence
is still the best path for relink fidelity because it carries source mob IDs. The
three modes serve different customers.

## Implementation note

The desktop helper — local audio extraction plus metadata upload — is Phase 4 work,
but the audio-only *API* path should exist earlier, with a documented manual export
procedure per NLE. That captures most of the value at a fraction of the cost.
