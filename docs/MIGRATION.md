# Migrating mishne.ai to another account or machine

Everything the project is currently lives in **one local git repository with no
remote**, plus two large directories that are deliberately not committed. There
is no cloud infrastructure, no database, and no deployment — which makes this
migration far simpler than it will ever be again.

**Do it before building Phase B.** Once there is a database, buckets and a
Step Functions deployment, the same move becomes an infrastructure project.

---

## What has to move

| | What | Size | How |
|---|---|---|---|
| 1 | The git repository | ~2 MB history, small tree | push to a new remote |
| 2 | `models/` — Whisper + diarization weights | 2.9 GB | re-download, do not copy |
| 3 | `samples/` — real test material | 443 MB | copy by hand; irreplaceable |
| 4 | Vendor API keys | — | reissue in the new account |
| 5 | This documentation set | in the repo | moves with it |

Nothing else exists. No infrastructure state, no secrets manager, no CI.

---

## 1. The repository

The repo currently has **no remote at all** — it has only ever existed on one
machine. Its last commits are the AAF ingest and `setup.sh` work; everything
from the multi-asset rework onward is **uncommitted working tree**.

```bash
cd ~/Dev/mishne.ai
git status --short          # expect a long list — commit it first
```

Commit before moving. Then:

```bash
git remote add origin git@github.com:<new-account>/mishne.ai.git
git push -u origin main --tags
```

Confirm `.gitignore` is doing its job before the first push — it must exclude
`models/`, `samples/`, `.env`, `node_modules/`, `.venv/`, `.next/`. A 3.2 GB
first push means it is not.

## 2. Models — re-download, never copy

`models/` is 2.9 GB of weights that are freely available. Copying them across
accounts is slower than fetching them and risks a partial file that fails in a
confusing way at load time.

`models/README.md` has the exact commands. In summary:

```bash
pip install -U "huggingface_hub[cli]"
hf download Systran/faster-whisper-large-v3 \
  --local-dir ~/Dev/mishne.ai/models/faster-whisper-large-v3
```

and the two ONNX diarization models (36 MB total, no account needed) from the
sherpa-onnx releases — the exact URLs are in `models/README.md`.

**Do not judge Hebrew output from a model below `medium`.** Word timestamps
drift and filler gets dropped, and removing filler is the product's job.

## 3. Samples — copy these by hand, they are the valuable part

`samples/` holds the only real material the project has been tested against:

- `SyncDaniel.aaf` — a 22-clip Hebrew sequence with embedded essence, and the
  only real production AAF that has been through ingest.
- `Gugu Mbatha-Raw interview rushes.mp4` — 25.7 minutes of English rushes.
- The `_roughcut/` output folders, including cached ASR JSON.

The cached `*.asr.json` files are worth keeping specifically: they let you re-run
the whole pipeline with `--replay` and no model, which is how most of the
development iteration was done.

Copy the directory as-is. If licensing prevents moving this material to a new
account, say so explicitly in the new repo, because otherwise the next person
will wonder why the samples referenced throughout the docs are missing.

## 4. Keys

Four optional vendor keys, any one of which is enough:

```
ANTHROPIC_API_KEY   OPENAI_API_KEY   GEMINI_API_KEY   XAI_API_KEY
```

Issue new ones in the new account and revoke the old. They are read from the
environment only and never from a file; nothing in the repo contains a key.
`apps/api/src/mishne/llm/README.md` covers the routing options.

With no key at all the pipeline still runs end to end, deterministically, using
the control scorer and enumerated spans — and says so at the top of every run.

## 5. Verify the move

Run this on the new machine. It exercises AAF ingest, transcription replay,
segmentation, span proposal, selection, assembly, all four exporters and
validation, without needing a model or a key.

```bash
cd apps/api
./setup.sh
.venv/bin/python -m pytest tests -q                       # expect 90 passed

cd ../../samples
../apps/api/.venv/bin/python ../apps/api/run.py SyncDaniel.aaf \
  --out /tmp/verify \
  --replay SyncDaniel_roughcut/work/SyncDaniel_flat_a0.asr.json \
  --target 40s --scorer heuristic --spans enumerate
```

Expected, and worth checking line by line:

```
23 beats · median 8.1s
already-cut material — 15 of 21 existing cuts used as beat boundaries
4 spans · 50s
12 validate  pass  AAF / FCPXML / EDL / OTIO
All artifacts validated.
```

Then the full path with a model, which also proves the weights downloaded
correctly:

```bash
../apps/api/.venv/bin/python ../apps/api/run.py SyncDaniel.aaf \
  --language he --model-path ../../models/faster-whisper-large-v3 --target 40s
```

Finally the web mockups:

```bash
npm install && npx tsc --noEmit -p apps/web/tsconfig.json && npm run dev -w apps/web
```

## Environment gotchas that will cost you an afternoon

- **Python must be 3.9-3.13.** OpenTimelineIO ships no 3.14 wheel and fails at
  import with `RuntimeError: bad any cast`. `setup.sh` selects a supported
  interpreter and verifies the adapters actually *register*, not merely import.
- **A virtualenv is not portable across platforms.** A `.venv` built on Linux is
  a set of dead symlinks on macOS, and vice versa. Never copy one; run
  `setup.sh`.
- **ffmpeg and ffprobe must be on `PATH`.** `setup.sh` checks.
- **large-v3 peaks around 4.6 GB of RAM.** In a 4 GB container it is killed with
  exit 137 and no useful message.
- `ortools` can be awkward to install in restricted environments. It is needed
  only for stage 8; everything up to selection runs without it.

## What is deliberately not being migrated

- **No infrastructure**, because none exists. When Phase B starts, the AWS
  account, buckets, database and state machine are created new in the target
  account and never migrated.
- **No `.env`.** Reissue keys.
- **No virtualenv, no `node_modules`, no `.next`, no `__pycache__`.**
- **No `work/` directories** beyond the cached ASR JSON. They regenerate, and
  the ingest cache is versioned — a cache written by older code is refused
  automatically rather than serving stale beats.

## Where to pick up afterwards

[HANDOVER.md](HANDOVER.md) for what exists and how it fits together, then
[roadmap/README.md](roadmap/README.md) for the ordered plan. Each roadmap file
is written to be opened alone, in its own session, without the others.
