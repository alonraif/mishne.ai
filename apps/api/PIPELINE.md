# Pipeline stages 0–4

Implemented and runnable. Media file in, structured beats out.

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
| 4 · structure into beats | `pipeline/steps/structure.py` | working, 14 tests |

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

## Known gaps

- **No diarization.** faster-whisper has none, and speaker labels are left empty
  rather than faked — one fabricated speaker would silently break the
  speaker-change rule in stage 4 and `speaker_priority` in stage 7. A managed
  provider or a separate diarization pass is needed before multi-speaker
  material works properly.
- **Retake detection is lexical**, using token-sequence similarity plus a
  phrase lexicon. It catches near-verbatim redelivery and announced retakes
  ("sorry, can I say that again"), which is most real cases. A paraphrased
  second attempt slips through — stage 6's redundancy clustering is meant to
  catch those.
- **Hebrew filler and retake lexicons are untested** against real material.
- Whisper tidies some speech toward written style, so filler may be
  under-detected. Worth measuring rather than assuming.
