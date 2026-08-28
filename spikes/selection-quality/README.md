# Spike B — selection quality

Answers one question: **does the engine select what a human editor actually
used?**

Spike A asked whether we can deliver a file an NLE will open. This asks whether
the file is worth delivering. It is the spike that decides whether the product
is worth building, and it should be settled before Phase 1.

## Run it

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt

.venv/bin/python spike.py fixtures/harbour.json
.venv/bin/python spike.py fixtures/harbour.json --diagnose      # scorer AUC
ANTHROPIC_API_KEY=... .venv/bin/python spike.py fixtures/harbour.json --scorer anthropic
```

## Ground truth is free

The expensive-looking part of this is getting labelled data — someone marking
which parts of three hours "should" make the cut. **Do not do that.** It is
slow, it is one person's opinion, and it is not what the product gets judged
against.

Every finished piece already carries its own ground truth. The editor's
sequence, exported as an EDL, AAF or XML — one menu command in any NLE — is an
exact record of which source timecode ranges went to air. That is a better label
than anyone would produce by hand.

So a corpus entry is two things a customer already has:

```
raw source (or just its audio)   +   the finished cut's EDL / AAF / XML
```

and nothing needs annotating. `corpus.py` turns that pair into a scored
comparison.

The same measurement keeps running after launch. In hybrid mode the diff between
what the engine proposed and what the editor shipped is this exact metric,
arriving on every job instead of in a one-off study. See
[ADR-0007](../../docs/adr/0007-selection-as-a-swappable-stage.md) — instrument
it from the first hybrid job.

## The metric

Everything is measured on the **time axis in frames**, not on beats. Beat
boundaries are our invention and the editor's in-points are not; comparing beat
sets would be grading our own segmentation.

| | |
|---|---|
| **recall** | of the time the editor used, how much did the engine also pick |
| **precision** | of the time the engine picked, how much did the editor use |
| **F2** | recall-weighted F — **the headline** |

Recall is weighted four times precision on purpose. A rough cut that runs
slightly long but contains every soundbite the editor wanted saves them an
afternoon. One that is exactly the right length and misses the best line wastes
their morning and costs their trust. The errors are not symmetric and a metric
that treats them as symmetric will drive the wrong decisions.

**Provisional thresholds:** >60% strong, 40–60% viable, <40% needs rework. They
stay provisional until human-to-human agreement is measured — see below.

## Baselines are the point

A raw score means nothing. "57%" could be excellent or it could be what you get
for free. Every run therefore scores the same material with four trivial
selectors — `random`, `uniform` (every Nth beat), `longest`, and `lead` (the
first N minutes, which is what a rushed assistant does).

**Lift over the best baseline is the number that decides the project.**

### What the current run shows

Against the bundled fixture, with the non-LLM control scorer:

| selector | recall | precision | F2 |
|---|---|---|---|
| longest | 73.1% | 69.8% | **72.4%** |
| engine (heuristic control) | 57.3% | 54.6% | 56.8% |
| random (mean of 5) | 30.1% | 29.8% | 30.0% |
| uniform | 29.0% | 28.3% | 28.9% |
| lead | 28.3% | 27.9% | 28.2% |

**`longest` is far stronger than anyone expects, and it beats the control.**
That is the most useful thing this spike has produced so far. Someone building
this without baselines would have seen 57%, compared it to nothing, and declared
the concept proven.

Why it works: people talk longest about what matters to them, and a beat an
editor keeps is usually a complete thought. Short beats are questions,
acknowledgements and half-sentences.

So the real question is not "does the engine beat random". It is **does the
language model beat `longest`** — and by enough to justify its cost and latency.
That is what `--scorer anthropic` measures, and it needs real material.

## Diagnosing a bad run

`--diagnose` reports **AUC**: the probability that a beat the editor used scores
above one they did not.

```
AUC 0.5    the scorer is noise
AUC 0.7    useful signal
AUC 0.85+  strong
```

Check this before touching the solver. A bad selection has two possible causes
and they need different fixes: the scorer cannot tell the classes apart (no
solver recovers from that), or it can and the solver picks wrongly.

## Findings

### 1. The solver objective must weight quality by duration

Maximising the sum of per-beat scores under a duration cap is a knapsack, and
knapsacks prefer many small items. Six mediocre five-second fragments beat one
excellent thirty-second answer on raw score, so the solver took the fragments —
producing a selection that was provably optimal and editorially worthless. It
scored **below random**.

Weighting each beat's score by its duration makes the objective "fill the cut
with the highest-quality material available", which is what an editor is doing
and is neutral to how long any beat happens to be. Fixed in `selection.py`;
this needs to be right in stage 7.

### 2. A wrong prior looks exactly like a broken pipeline

The control scorer's first version peaked its length feature at the median, on
the theory that extremes are suspect. For interview material that is backwards,
and the result was **AUC 0.335 — reliably worse than chance.**

Without the diagnostic this reads as "the concept doesn't work". With it, it
reads as one wrong line. Changing the prior moved AUC to 0.795 and F2 from 14.5%
to 56.8%, with no change to the solver.

Worth remembering when the LLM scorer first runs and scores badly: measure
separation before concluding anything about the concept.

### 3. Human-to-human agreement is the ceiling, and it is unknown

Two editors given the same rushes do not produce the same cut. 55% against a 60%
human ceiling is a very different result from 55% against a 90% ceiling, and at
present nobody knows which it is.

If two independent cuts of the same source can be obtained, measure it. Until
then the absolute thresholds above are guesses and **lift is the trustworthy
signal**.

## Limits of the bundled fixture

`fixtures/harbour.json` is hand-authored: ~40 minutes of interview, 90 beats, a
13-beat human cut. It exists to prove the harness and to check the metric
discriminates. It **cannot** validate the product.

Two specific cautions:

- **Length and quality correlate more strongly here than in real rushes**,
  because the good quotes were written as the long ones. That inflates
  `longest`. Real material will weaken the correlation — but probably not
  eliminate it.
- Base rate is 25.7% of spoken material, higher than a real 3-hour-to-10-minute
  job (closer to 10%). A lower base rate makes every selector's raw score fall,
  which is another reason to read lift rather than absolutes.

Replace it with real pairs as soon as two or three exist. That is the whole
point, and per "ground truth is free" above it costs a customer one export.

## Files

| | |
|---|---|
| `spike.py` | CLI and report |
| `metrics.py` | The metric. Interval arithmetic, recall/precision/F2, lift |
| `corpus.py` | Loading a pair; ground truth from an editor's own EDL/AAF/XML |
| `scorers.py` | Heuristic control and the real Anthropic scorer |
| `selection.py` | Stage 7 CP-SAT solver, plus the four baselines |
| `diagnose.py` | Scorer separation (AUC) — read this first when a run is bad |
| `fixtures/` | Hand-authored pair and its build script |

## What is needed next

1. **Two or three real pairs.** Raw source plus the finished cut's EDL. This is
   the blocker; everything else is built.
2. Run `--scorer anthropic` against them and compare to `longest`.
3. Measure human-to-human agreement if two cuts of one source can be found.
