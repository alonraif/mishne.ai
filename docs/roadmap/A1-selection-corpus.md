# A1 — Selection corpus and the quality number

> Thread starter. Read [../HANDOVER.md](../HANDOVER.md) first for project
> context; you should not need any other file.

## Goal

Get a corpus of raw material paired with the editor's own finished cut, and
produce one number: **how much of what the editor kept does the system also
keep?** Then use that corpus to settle the thresholds that are currently guesses.

## Why this is first

Three separate open questions in this project are the same question wearing
different hats:

1. Is text-only selection good enough that an editor keeps the cut? (Spike B)
2. Are the span-proposal thresholds right? (ADR-0010: 300 ms of silence, 12 s
   before carving, 2 s minimum span — all chosen by looking at two clips)
3. Which model is genuinely better per task? (ADR-0011: compliance is measured
   exactly, editorial taste is not measurable at all)

One corpus answers all three. Nothing else in the roadmap unblocks more.

It also needs **no infrastructure**. It is a data-gathering problem plus a
harness that already exists.

## What already exists

- `spikes/selection-quality/` — the harness, built and working against synthetic
  fixtures. `metrics.py` (recall-weighted F2, lift over baselines), `corpus.py`,
  `scorers.py`, `selection.py`, `diagnose.py` (AUC), `fixtures/harbour.json`.
- `apps/api/run.py` — produces `<name>.mishne.json` per run, containing every
  cut with its source timecodes. That is one half of the comparison.
- Two pieces of real material in `samples/`, **neither with a reference cut**.

## What to build

1. **Get the material.** Three to five pieces where both the rushes and the
   finished piece exist. LiveU customers are the obvious source; a production
   house that will share an old project is just as good. What you need per piece:
   the rushes, and the editor's EDL/AAF/XML of the finished cut. The finished
   *video* is not enough — you need the cut list to compare timecodes.
2. **An importer** that reads a reference EDL/AAF and produces the ground-truth
   ranges in source timecode. Most of this exists: `pipeline/steps/aaf_ingest.py`
   already parses an AAF into clips with source ranges.
3. **The metric.** Temporal overlap between selected ranges and reference ranges,
   as a fraction of the reference cut. Already defined in
   `spikes/selection-quality/metrics.py` — verify it against a hand-computed
   example before trusting it.
4. **Baselines**, so the number means something: random selection of the same
   duration, first-N-seconds, and highest-confidence-first. A system that cannot
   beat "take the first two minutes" is not a system.
5. **A threshold sweep** over the ADR-0010 constants, reported as a table.
6. **A model comparison**: same corpus, same brief, each of the four vendors,
   reported as quality against the per-job cost the router already computes.

## Decisions already made

- The metric is **recall-weighted**. Missing something the editor used is worse
  than including something they did not — the editor can delete a clip in
  seconds and cannot recover material they were never shown.
- Ground truth is the editor's EDL, not a human rating of the output. Ratings
  are expensive, slow, and not reproducible.
- Selection is a swappable stage (ADR-0007), so a better selector can be dropped
  in without touching anything else.

## Decisions still open

- **What number is good enough to sell.** The roadmap doc suggests >60% overlap
  is strong and <30% means the concept needs rethinking. That was written before
  any measurement; treat it as a hypothesis.
- Whether to weight by beat prominence rather than raw seconds.
- Whether one number per piece is enough, or the distribution matters more.

## Traps

- **The reference cut usually contains material the rushes do not** — b-roll,
  graphics, music, pickups from another day. Match on source MobID or tape name
  first, then compare timecodes, and report unmatched reference clips separately.
  Counting them as misses will make the system look far worse than it is.
- **The editor's cut is one valid answer, not the only one.** Two good editors
  overlap maybe 60-70% with each other. Establish that ceiling if you can get two
  cuts of the same material; otherwise state the caveat every time the number is
  quoted.
- **Do not tune on the whole corpus.** Hold out at least one piece, or the
  thresholds will fit the sample and nothing else.
- The control scorer in `score.py` is deliberately weak and says so at runtime.
  Do not benchmark against it and conclude anything.

## Definition of done

- A corpus in the repo (or a documented private location) with at least three
  pieces, each with rushes and a reference cut list.
- One command produces the overlap number per piece and against each baseline.
- A table of the ADR-0010 thresholds against quality, and a chosen value for
  each with the evidence in the ADR.
- A table of the four vendors against quality and cost per job.
- ADR-0010 and ADR-0011 updated: their "Open" sections closed or narrowed.
