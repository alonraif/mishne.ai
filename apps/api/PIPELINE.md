# The pipeline

**All twelve stages are implemented.** One command takes a media file to a
delivered rough cut.

```bash
python run.py rushes.mov --notes "Ten minutes, tight. Lead on the closure."
python run.py interview.mov --target 6m --language he --model large-v3
python run.py rushes.mov --replay work/rushes_a1.asr.json   # no model needed
```

Produces, in `--out`: `.aaf`, `.fcpxml`, `.edl`, `.otio`, a self-contained
`.transcript.html`, and a `.mishne.json` record of every decision. **Hand over
the whole folder** — the transcript page is what earns an editor's trust.

## Hebrew and right-to-left

Hebrew is a first-class target and it breaks assumptions English-only code makes
silently. No capitalisation, so any heuristic keyed on capital letters returns
nothing quietly. Different filler and retake phrasing. And RTL layout with LTR
runs inside it — a Hebrew transcript routinely contains Latin names, numerals
and timecode.

Three rules, learned from getting them wrong first:

- Transcript text uses `dir="auto"` **per string**, because one sentence mixes
  scripts. A blanket direction on the page does not handle it.
- **Timecode is forced LTR with `unicode-bidi: isolate`.** `10:02:14:00` in an
  RTL paragraph reorders around its colons without it, and timecode is exactly
  what an editor scans for.
- English UI strings inside an RTL container need `dir="auto"` too, or their
  trailing full stop jumps to the front of the line.

**Whisper needs a bigger model for Hebrew.** `base` is fine for English and poor
for Hebrew; `medium` or `large-v3` is the realistic floor. `run.py` warns when
the model is too small for the language.

The Hebrew filler and retake lexicons are a first pass written without native
review. The retake one already caught a real miss — `אפשר להגיד את זה שוב`, the
commonest way of asking for another take, did not match the original fixed
phrases and an announced retake reached the cut. Worth checking both against
real material.

## Stages 0–4 (ingest)

Media file in, structured beats out.

```bash
cd apps/api
python3 -m venv .venv && .venv/bin/pip install -r requirements-pipeline.txt

.venv/bin/python ingest.py rushes.mov --out work/
.venv/bin/python ingest.py interview.wav --rate 25 --language he
.venv/bin/python ingest.py rushes.mov --skip-asr          # stages 0, 1, 3 only
.venv/bin/python -m pytest tests/ -q
```

| Stage | Module | Status |
|---|---|---|
| 0 · probe and normalize | `pipeline/steps/prepare.py` | working |
| 1 · extract audio | `pipeline/steps/audio.py` | working |
| 2 · transcribe | `pipeline/steps/transcribe.py`, `asr/` | working; needs a model |
| 3 · silence map | `pipeline/steps/vad.py` | working, fully offline |
| — · speaker attribution | `pipeline/steps/speakers.py` | working, 11 tests |
| 4 · structure into beats | `pipeline/steps/structure.py` | working, 14 tests |
| 5 · compile brief | `pipeline/steps/brief.py` | working; LLM optional |
| 6 · score beats | `pipeline/steps/score.py` | working; control + Claude |
| 7 · solve selection | `pipeline/steps/select.py` | working, CP-SAT |
| 8 · review sequence | — | not implemented |
| 9 · refine cut points | `pipeline/steps/refine.py` | working |
| 10 · assemble timeline | `pipeline/steps/assemble.py` | working |
| 11 · emit artifacts | `pipeline/steps/emit.py` | working |
| 12 · validate | `pipeline/steps/validate.py` | working |

## What it produces

`<name>.beats.json`, in the format the selection-quality spike reads. Add the
editor's cut to `human_cut` — from their own EDL, no annotation — and run:

```bash
python spikes/selection-quality/spike.py apps/api/work/rushes.beats.json --diagnose
```

That is the path from a real interview to a real quality number.

## Notes that will save an hour

**Transcription needs a Whisper model.** faster-whisper pulls it from
HuggingFace on first use. If that host is blocked you get a proxy 403 with no
useful message — allowlist `huggingface.co`, or fetch the model once and pass
`--model-path`. In production the model belongs baked into the worker image; a
cold start that downloads 1.5 GB is not a cold start you want against a job SLA.

**CPU transcription runs at roughly real time or worse.** Three hours of audio
is about three hours of compute. Fine for benchmarking, not for a product — this
is the argument for a managed provider or GPU.

**Audio-only input has no frame rate**, so `--rate` is required for it. The code
raises rather than guessing: a silently assumed rate puts every cut a frame out
and the cause is three steps away.

**VAD is fully offline.** Silero ships as an ONNX model inside faster-whisper —
no download, no torch.

## Verified against real media

Stages 0, 1 and 3 were run against the Spike A test masters. Stage 0 read back
the exact `09:58:02:00` start timecode Spike A wrote, at 25/1, and VAD correctly
found *no* speech in tone-only audio rather than hallucinating segments. The
full 0–4 chain was exercised offline through the replay provider.

## Speakers

Two problems, routinely confused:

**Attribution** — who spoke when — is automatic. On **multi-track** material it
is deterministic and better than any model: each subject is on their own lav, so
whoever is loudest on track 2 is the person wearing mic 2. Stage 1 already
extracts per track, so this is arithmetic, not inference. Two details make it
work: each track is normalised by its own speech level (otherwise the hottest
mic wins every word), and a word is attributed only when the leader clears the
runner-up by ~3.5 dB (otherwise bleed decides). Inside that margin it is flagged
as crosstalk, and above 25% crosstalk the whole attribution is marked unreliable.

**Naming** is not automatic and never will be. Diarization returns `Speaker_00`;
no model knows it is Margret. Speakers carry `defaultLabel` ("Mic 2") until a
person renames them in the UI, and `confirmed` records that they did. **An
unconfirmed name must never reach a delivered artifact** — a misattributed quote
in a broadcast piece is a serious error, not a typo.

## Known gaps

- **Single-track material has no diarization.** faster-whisper has none, so
  every word goes to one speaker and the run says so explicitly rather than
  inventing voices. Multi-speaker single-track needs a managed provider or a
  pyannote pass; expect 8-15% DER, worse with crosstalk.
- **A bleed-only track ties.** A mic that never carries its owner's voice has no
  real speech to set a reference from, so its bleed becomes its own reference.
  Flagged as crosstalk rather than guessed. Documented in tests/test_speakers.py.
- **Retake detection is lexical**, using token-sequence similarity plus a
  phrase lexicon. It catches near-verbatim redelivery and announced retakes
  ("sorry, can I say that again"), which is most real cases. A paraphrased
  second attempt slips through — stage 6's redundancy clustering is meant to
  catch those.
- **Hebrew filler and retake lexicons are untested** against real material.
- Whisper tidies some speech toward written style, so filler may be
  under-detected. Worth measuring rather than assuming.
