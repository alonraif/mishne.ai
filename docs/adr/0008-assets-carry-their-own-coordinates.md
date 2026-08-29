# ADR-0008 — Assets carry their own coordinates; there is no virtual timeline

**Status:** Accepted · **Date:** 2026-08-29

## Context

A media project is long. Footage arrives over weeks — an interview on Tuesday, a
pickup shoot a fortnight later, an archive pull the day before delivery — and one
finished piece is cut from several of them. The pipeline as first built could cut
ten pieces from one upload but not one piece from three, which is the wrong way
round for how the work actually happens.

Making a job draw on several uploads has one obvious implementation: lay the assets
end to end, give every beat a position on a single virtual timeline, and carry on as
before. It is obvious because it makes the multi-asset case look exactly like the
single-asset case everywhere downstream.

## Decision

**No virtual timeline.** A beat keeps its own asset's local timing and carries
`asset_id`. Every stage that needs a position resolves it against that asset's own
frame rate, start timecode, silence map and media extent.

The pipeline splits along a seam this makes explicit:

    per ASSET, once, cached forever    stages 0-4 and speaker attribution
    per JOB, across chosen assets      stages 5-8  (brief, score, select)
    per JOB, mapping back per asset    stages 9-12 (refine, assemble, emit)

Ordering across assets is `(upload order, start)` — the only honest reading of
"chronological" for material shot on different days.

Frame rates are conformed to the sequence rate exactly once, in `assemble._conform`,
and only because the AAF writer refuses a document whose clips disagree with it.
Mixed rates are reported to the editor rather than silently absorbed.

## Rationale

- **The conversions do not cancel.** Cut-point refinement needs an asset's own
  silence map; assembly needs its own timecode and rate. Fabricating global
  coordinates means converting into them and back out at every stage, and the bug
  when one conversion is wrong is a timeline that opens cleanly and shows the wrong
  frames. Nobody downstream can see that happen.
- **Frame numbers are per file and they collide.** Two unrelated reels look
  contiguous near their starts. A merge rule that does not check asset identity will
  eventually splice footage shot months apart into one clip.
- **Transcription belongs to the asset, not the job.** It is the expensive step. An
  upload transcribed today is re-used by a job next month at no cost, which is what
  makes separated uploads affordable at all. A virtual timeline would key the cache
  to the combination of assets and throw that away.
- **The seam is where the product is.** "Add a fourth reel and re-cut" is a cheap
  operation precisely because stages 0-4 do not re-run.

## Consequences

- `jobs.asset_id` becomes the `job_assets` join table; `beats` and `selections`
  carry `asset_id`; `transcripts` are keyed on the asset.
- Nothing in the UI may render a position without knowing which asset it belongs to.
  `assetOf(transcript, beat)` exists so no component reinvents the lookup.
- **The same person in two uploads is two speakers until a human says otherwise.**
  Attribution knows which microphone a voice came down and nothing about whether
  Tuesday's track 1 is Friday's track 1. Merging is a row a person creates
  (`speaker_links`), surfaced in the speaker legend. This looks like a shortcoming
  and is the safe direction to be wrong in: the merge is one click, and an invented
  merge puts words in the wrong mouth in a delivered cut.
- Mixed frame rates in one cut are permitted and reported, not refused and not
  hidden. NLE behaviour on a mixed-rate AAF is a live risk and untested in Avid.

## Alternatives considered

**One virtual timeline.** Rejected above.

**Refuse mixed rates.** Tempting, and wrong: two cameras at different rates on one
shoot is ordinary, and the editor is better served by a cut plus a warning than by a
refusal.

**Cluster speaker embeddings across assets to merge automatically.** Deferred. The
failure mode is silent and lands in a delivered artifact; a suggestion a person
confirms would be an acceptable future middle ground.
