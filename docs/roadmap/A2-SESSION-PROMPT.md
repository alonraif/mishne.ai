# A2 — session prompt

Paste the block below into a fresh session, from the repo root.

---

```
We are working on mishne.ai. This session has one job: workstream A2 — proving
our generated AAFs work in Avid Media Composer, and fixing them if they don't.

Read these two files first, in this order, and nothing else until you have:
  docs/HANDOVER.md                     — what exists, how to run it, the traps
  docs/roadmap/A2-nle-acceptance.md    — this workstream's brief

Do not re-derive the architecture or re-read the whole codebase. The handover
is accurate and current; trust it.

## The situation

Every deliverable this product makes is an interchange file, and Media Composer
is the one NLE nobody has tested. DaVinci Resolve is confirmed by hand.
Premiere and Final Cut are unconfirmed but lower risk. If Avid rejects our
files there is no product, so this is the highest technical risk in the
project — and it has nothing to do with AI.

I have the Avid access. You cannot open Media Composer, so the loop is:
you generate files and tell me exactly what to check, I open them and report
back, you diagnose and fix. Optimise for making my turn in that loop short and
unambiguous — I want to read numbers off a screen, not form opinions.

## What already exists — do not rebuild it

  spikes/aaf-roundtrip/         hand-built 20-cut timelines, four formats,
                                four rates, automated checks passing.
                                `spike.py all` writes out/<rate>/ plus a
                                CHECKLIST-<rate>.md with expected values.
  apps/api/run.py               the real pipeline; produces validated AAF,
                                FCPXML, EDL, OTIO from real footage
  interchange/validate.py       independent-parse validation, stage 12
  samples/SyncDaniel_roughcut/  real output from a real production AAF

## Order of work

1. **Extend the test matrix to the cases the spike does not cover.** The spike
   tests hand-built timelines. The cases that actually worry me are all in real
   pipeline output:
     a. an AAF generated FROM an AAF, inheriting the source MobIDs — this is
        supposed to relink silently in the project it came from, and that claim
        has never been tested. samples/SyncDaniel.aaf is the input.
     b. a cut whose clips span source joins, which assembly deliberately splits
        into several clips
     c. a mixed-rate project, which assembly conforms to the sequence rate. NLE
        behaviour here is unknown and the run already warns about it
     d. embedded essence vs linked media — they fail differently
   Produce these as a named set of files with one checklist per file, in the
   same style as the spike's CHECKLIST-<rate>.md: exact expected timecodes per
   clip, so verification is reading numbers.

2. **Build the structural diff tool, before I report anything back.** This is
   the highest-leverage thing you can do without Avid. I will export a simple
   3-clip sequence from Media Composer as a native AAF and put it in
   samples/. Write a tool that dumps an AAF's structure — mobs, MobIDs, slots,
   source clips, rates, essence descriptors — and diffs ours against Avid's
   own. If Avid rejects our file, this tool tells us why in minutes instead of
   days, and it is also the foundation of the documented fallback (writing AAF
   with pyaaf2 against a known-good reference).
   Tell me when you need that reference export and exactly how to make it.

3. **Then the acceptance run.** Give me one document: every file to open, in
   order, with what to check per file and the expected values. I work through
   it and report back. Then you diagnose.

## Rules

- Fix problems in the projection layer, never by hand-editing an output. OTIO
  is the record of truth (ADR-0001).
- Validation is by independent parse. Never trust the writer's own word that it
  wrote something correctly.
- Read the "things that will bite you" section of docs/HANDOVER.md before
  touching interchange code. Several of those traps are specifically about AAF
  and MobIDs, and each one cost real time to find.
- Record results in docs/architecture/02-media-and-interchange.md and in
  spikes/aaf-roundtrip/README.md, including application versions.
- If Avid rejects the files and the projection layer cannot fix it, say so
  plainly and scope the pyaaf2 fallback rather than trying to make it work.

## Environment

  cd apps/api && ./setup.sh     builds the venv, checks interpreter and ffmpeg
  Python must be 3.9-3.13       OTIO has no 3.14 wheel; setup.sh pins this
  .venv is not portable         never copy one between machines, rerun setup.sh

Run the pipeline without a model or an API key using the cached transcript:

  cd samples
  ../apps/api/.venv/bin/python ../apps/api/run.py SyncDaniel.aaf \
    --out /tmp/a2 --replay SyncDaniel_roughcut/work/SyncDaniel_flat_a0.asr.json \
    --target 40s --scorer heuristic --spans enumerate

Expect: 23 beats, 4 spans, all four artifacts validating. If that does not
reproduce, stop and fix the environment before anything else.

Start with step 1. Ask me for the Avid reference export as soon as you know
what you need from it.
```
