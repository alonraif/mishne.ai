# ADR-0020 — A preview rendition per asset, built beside the pipeline

**Status:** Accepted · **Date:** 2026-09-02

## Context

`00-overview.md` listed video proxy generation among the things deliberately out
of scope, with a reason that was good at the time: *"only needed once there is
an in-browser review player, and the MVP deliverable is a downloadable timeline,
not a preview."* ADR-0005 recorded the same thing as a known cost of audio-only
ingest — no path to a review player without a separate proxy upload.

Both were written before the cut editor existed. C2 made the transcript into a
screen where a person **chooses** the lines, and that changes what the material
is for. Read as text, a transcript says what was said and nothing at all about
whether the take was usable: you cannot hear the hesitation before the answer,
you cannot tell the third attempt from the first, and you cannot see the moment
the subject looks off camera. `07-job-modes.md` offers "a frame-accurate
transcript they can cut on rather than scrubbing three hours of rushes" — that
is the right pitch for *finding* a line and the wrong one for judging it.

So the editor needs a player, the player needs something small and seekable to
play, and neither exists.

## Decision

**Every asset gets one preview rendition, keyed on the asset, stored in the
derived bucket.**

720p H.264 at CRF 26 under a 1 Mbps ceiling, `-preset superfast`, AAC mono at
64 kbps, `faststart`, keyframes every two seconds. The preset is a storage
decision rather than a quality one: CRF holds the picture steady, so a faster
search just spends more bits reaching it — measured on a real 25-minute 360p
interview, superfast halves the encode against veryfast for about 45% more
file. On an already-compact source that can leave a preview no smaller than
the original; on the ProRes masters this exists for, it is still two orders of
magnitude down. Audio-only sources, and sequences, get AAC alone. A
three-hour rush lands near 1.4 GB of video or 90 MB of sound.

Keyed on the asset and not the job, for the reason transcription is (ADR-0008):
it is derived from the asset's own bytes, it is identical for every job that
draws on that asset, and a second job next month should find it already made. It
is **not** an `artifacts` row — an artifact is a deliverable, one per job per
kind, and this is neither.

**The expensive case is built beside the pipeline, not inside it.** `probe` puts
a flat upload in a queue the moment its bytes are known to be readable — before
any job exists — and `orchestration/proxyrunner` drains that queue in its own
process while the worker gets on with transcribing. A preview is not a stage: it
produces nothing the cut depends on and nothing downstream reads it, so making
every job wait on minutes of x264 would be paying for it in the one place that
must not be slower.

**A sequence is the exception, and structurally so.** An AAF has no playable
programme. What there is to preview is the mix of its sound tracks, and that
render only exists once stage 0 has done it (ADR-0019) — so
`project.stage_prepare` encodes its own flattened WAV and publishes it with the
rest of that asset's derived files. Doing it in the runner instead would mean
re-running the most expensive part of ingest, in a second place, from a second
implementation of stage 0.

That asymmetry is deliberate rather than tidy: the parallel path exists for the
cost that is worth parallelising, and a ~30-second AAC encode of a file already
on disk is not it.

**The preview never decides whether ingest succeeded.** `proxy_status` is its own
column, not a value of `assets.status`. An asset is ingestable long before it is
playable, and failing somebody's upload over a codec ffmpeg could not open would
be a catastrophe manufactured out of an inconvenience.

## The rule the whole feature rests on

**Position in the preview equals position in the source, exactly.** The player
maps a media element's `currentTime` to a beat's source timecode as
`start_tc + elapsed`, with no correction term. So:

* the frame rate is never resampled — no `-r`, no `fps` filter;
* and the result is **measured** rather than assumed. `proxy.verify` re-probes
  the finished file and refuses it if the duration has moved by more than a
  frame.

That check is the same instinct as stage 11 re-parsing every artifact it writes.
Nothing in ffmpeg's exit status distinguishes a correct transcode from one that
quietly changed the frame rate, and the symptom of the second is not an error —
it is a highlight that is a second and a half ahead of the voice an hour into a
three-hour interview, which reads as "the player is broken" and points nowhere
near the encoder. On real 25-minute footage the measured drift is 19 ms.

## Consequences

**A URL that is held rather than spent.** `presign_ttl_seconds` is 900, tuned
for a download followed within seconds. A preview URL sits inside a `<video>`
for as long as somebody has the editor open, and when it expires the element
reports a *decode* error — nothing about credentials anywhere. Hence
`proxy_presign_ttl_seconds` at six hours, and a client that re-mints on `error`.

**Bucket CORS grew the range headers.** Seeking is ranged GETs, and a
cross-origin response exposes no header the bucket has not named. The failure is
the same shape as the missing `ETag` was: every request succeeds and the player
reports a decode error.

**Compute is spent before anyone approves a cap.** The transcode starts at probe
time, so an org can upload material, never cut it, and still cost real CPU. ADR-0006
says money moves through the ledger only, so this cannot stay unpriced for ever.
For now it is absorbed into the tier's per-source-hour rate — it is small beside
ASR — and charging per job would be wrong on the facts, since the cost is
incurred per asset. **This is the open question in this decision.**

**A warm ingest cache has no preview.** An asset transcribed before this existed
re-runs from `ingest.json` and never reaches `stage_prepare`, so a sequence
ingested earlier stays without one. Bumping `CACHE_VERSION` would fix it by
re-transcribing every asset in the system — orders of magnitude more expensive
than the thing being gained. The editor says there is no preview, which is true.

**Not decided here:** playing the *cut* — the selected beats, in order, as one
stream. That needs a playlist over per-asset previews and is a separate piece of
work.

## Alternatives considered

**A stage in the registry.** Simplest to reason about and it is what the step
list is for, but it puts the transcode on the critical path of the transcript,
which is the thing the customer is actually waiting for.

**Transcode at upload, in the browser.** Removes the server cost entirely and
fails on the material that matters — a 200 GB ProRes master is not something a
browser is going to re-encode, and the customers with those are the ones paying.

**Stream the original with ranged reads.** No transcode at all, and no browser
plays ProRes or MXF. It also moves 200 GB across the wire to show somebody a
ten-second line.
