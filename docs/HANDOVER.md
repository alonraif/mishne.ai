# mishne.ai — state of the project

*Last updated 2026-08-30 (B1–B4; C3 cost and telemetry, C1 billing).*

**Read this first if you are picking the project up cold**, in a new session, on
a new machine, or in a new account. It says what exists, what it does, what it
does not do, and where everything lives. Then go to
[roadmap/README.md](roadmap/README.md) for what to build next; each workstream
there is written to be started in its own thread without this one.

---

## What the product is

Content creators and broadcast professionals upload long raw video — or an AAF
with embedded media — add production notes, and get back a **rough cut**: an
A-roll assembly they open in their own NLE and finish by hand.

It is deliberately **not** a fine-cut tool. It targets the heaviest lift in
post: getting from three hours of rushes down to the ten minutes that will
actually make the cut.

**The AI never touches audio or video.** Speech is transcribed with accurate
timestamps, every editorial decision is made on text, and an AAF is generated
from the result. That constraint is the reason the output is frame-accurate and
inspectable, and it should not be relaxed without a very good reason.

---

## Running the whole thing on one machine

    ./dev.sh

Postgres and MinIO, the schema, the application login role, the three buckets
with their CORS and lifecycle rules, and then the API, the web app and a job
runner. `./dev.sh setup` stops before the processes; `./dev.sh api`, `./dev.sh web` and
`./dev.sh worker` each run one of them in its own terminal.

Access is by invitation. `/signup` is closed unless `PUBLIC_SIGNUP=true`, which
is how the first owner of a deployment is made — sign up once, then turn it off.
After that an owner invites from the Team screen, and with `MAIL_PROVIDER=console`
the invitation and its link print to the API's terminal.

The job runner is the piece that is easy to miss: `worker.py` takes one job id,
because in production Step Functions decides what runs. Locally nothing decided,
so a job submitted in the browser sat at `queued` under a progress panel that
never moved. `orchestration/devrunner.py` is the missing half of that loop and
is local-only by construction.

## Where it stands

A **working concierge pipeline**. One command turns real footage into an
editable rough cut with four interchange artifacts and a transcript page:

```bash
cd apps/api
./setup.sh                                    # venv, checks interpreter + ffmpeg
.venv/bin/python run.py ../../samples/SyncDaniel.aaf --language he --target 40s
```

Transcription is a managed API (ADR-0018): xAI for the languages it publishes,
Gemini for Hebrew and everything else, routed by language, ~$0.10-0.30 per
source hour. Self-hosted Whisper is still supported and is one flag —
`--asr faster-whisper --model-path ../../models/faster-whisper-large-v3` — which
is what an air-gapped customer runs and what the CPU baseline was measured on.

Verified on two pieces of real material:

| | SyncDaniel.aaf (Hebrew) | Gugu interview (English) |
|---|---|---|
| Source | 3.7 min, 22-clip sequence, embedded essence | 25.7 min, single-file rushes |
| Transcript | 407 words, large-v3, mean confidence 0.96 | 4542 words, large-v3 |
| Beats | 23, median 8.1s | 61, median 27.3s |
| Candidates | 25 | 232 |
| Cut at target | 4 spans, 50s | 14 clips, median 10.2s |
| Artifacts | AAF, FCPXML, EDL, OTIO — all validate | all validate |

90 tests pass. Everything above runs on one machine with no cloud dependency.

### What does *not* exist

- ~~No database.~~ **Postgres, as of B1.** Twenty tables, `org_id` on every one
  of them, row-level security enabled and forced in the migration that creates
  each table. `use_mocks: True` still serves fixtures and is refused outside
  `environment=local`. See `apps/api/migrations/README.md` before writing a
  second migration — the expand/contract rules are not optional.
- ~~No object storage, no upload path.~~ **Both, as of B2.** Three buckets by
  lifecycle, presigned multipart upload, a resumable browser client that
  survives a closed laptop, probe-on-arrival, lifecycle and CORS rules in
  `infra/`, and linked AAFs that ask for the media they reference (ADR-0014).
  Media never transits the API and probing does not run in it.
- ~~No orchestration.~~ **A durable runner, as of B3.** The same fifteen stages
  `run.py` runs, executed with per-stage retries, progress into `job_steps`,
  cancellation between steps, and a credit hold that is settled or released.
  Resume is idempotent re-execution rather than a checkpoint restore (ADR-0016):
  a re-run performs zero transcription. The Step Functions definition is
  generated from the registry into `infra/statemachine.json`, and a worker image
  is in `apps/api/Dockerfile`. **Not deployed** — no Terraform, no AWS account;
  what exists runs and is tested locally.
- ~~No auth, no tenancy.~~ **Both, as of B4.** Signup, login, logout, SSO
  through WorkOS behind a provider interface, sessions in an httpOnly cookie,
  three roles, and an audit log. The org on a request comes from the session and
  never from a header; `app.org_id` is set per transaction and the policies do
  the isolation. See ADR-0015 and `tests/test_pool_isolation.py`.
- ~~No live billing.~~ **Stripe, as of C1.** Checkout behind a payment provider
  interface, credits granted on the webhook and never on the redirect, deduped
  on the Stripe event id, and a `FakeProvider` that signs and verifies for real
  so the whole path is testable with no account or network. Per-project spend
  and a low-balance warning whose threshold scales to what the org's own jobs
  cost. **Not connected to a Stripe account** — `payment_provider` is `fake`
  until keys exist.
- ~~Nothing measures cost.~~ **The schema does, as of C3.** Migration 0005 adds
  per-asset timings, a cache flag and per-stage model spend to `job_steps`, plus
  a `job_llm_calls` row per model call; `jobs.cost_cents` finally has a writer
  and is a projection of those rows. OpenTelemetry spans, one per attempt, with
  the exporter behind config so no vendor is named. Alerting that pages on a job
  out of retries and stays quiet for one that merely retried.
  `python -m mishne.report --org ORG JOB_ID` reads it back.
  **The number still does not exist**: no job has run with a vendor key and a
  model scorer, so `job_llm_calls` is empty and `--baseline` says so.
- **The web app is mockups.** Ten screens against fixtures in `src/lib/`, no API.
- **No deployment of any kind.** No infra, no CI, no environments.

**Tests: 367 pass**, including the reference run, which needs the sample AAF and
its stored ASR response (see below).

---

## Layout

```
apps/api/                 the pipeline and the (mock-backed) API
  run.py                  the concierge CLI — all stages, one command
  setup.sh                venv with interpreter/ffmpeg/adapter checks
  src/mishne/
    pipeline/steps/       the twelve stages, one file each
    pipeline/project.py   multi-asset orchestration and the ingest cache
    asr/                  transcription: two managed engines, routed by
                          language, plus self-hosted Whisper and replay
    diarize/              single-track voice separation, ONNX
    llm/                  four vendors behind one interface + routing
    interchange/          MobIDs, FCPXML patch, round-trip validation
    timecode.py           rational rates, drop-frame, the proven conversion pair
    routers/, mock.py     FastAPI surface; fixtures behind use_mocks
    auth/                 providers, sessions, password hashing
    orchestration/        the runner, the step graph, the state machine, the worker
    audit.py              who did what, append-only
    storage.py            three buckets, the key scheme, presigned multipart
    workspace.py          objects in, real files on disk, derived files back out
    db/uploads.py         the upload lifecycle as writes
    db/                   models, session, query layer, seed script
  migrations/             Alembic — 0001 is the whole schema, and README.md
                          is the expand/contract contract
  Dockerfile              the worker image: ffmpeg, the models, non-root
  tests/                  300 tests
apps/web/                 Next.js 15 mockups, Tailwind 4, shadcn/ui
packages/shared/          types, timecode, billing, RTL direction — TS
spikes/aaf-roundtrip/     Spike A: interchange, automated, passing
spikes/selection-quality/ Spike B: harness built, no corpus yet
docs/architecture/        00-07, the design
docs/adr/                 0001-0011, the decisions and why
models/                   Whisper + diarization weights (gitignored, 2.9 GB)
samples/                  real test material (gitignored, 443 MB)
```

## Running it

Everything below is run from `apps/api` with its venv. `alembic`, `pytest` and
`python` are inside it and not on your PATH — either prefix them with
`.venv/bin/` or `source .venv/bin/activate` first, which is the single most
common five minutes lost by someone picking this up.

```bash
cd apps/api
./setup.sh                                     # venv, ffmpeg and interpreter checks
docker compose -f ../../infra/docker-compose.yml up -d
.venv/bin/alembic upgrade head                 # migrations run as the OWNER
.venv/bin/python -m mishne.db.bootstrap        # creates the app role RLS applies to
.venv/bin/python -m mishne.db.seed --reset     # the fixtures, as real rows
.venv/bin/python -m pytest -q                  # 300 tests
```

The API and the web app:

```bash
npm run api        # FastAPI on :8000  — USE_MOCKS=false to talk to Postgres
npm run dev        # Next.js on :3000
```

### The concierge path

One machine, no cloud, no database. This is what the pipeline is measured
against and what `test_reference_run.py` compares the orchestrator to:

```bash
.venv/bin/python run.py ../../samples/SyncDaniel.aaf --language he --target 40s
```

### The platform path

Each of these is a separate process on purpose. Media never transits the API,
and probing means reading the object:

```bash
# Stage 0 when an object lands. In production this is an S3 event calling
# mishne.probe.handle_s3_event; locally it is this.
.venv/bin/python -m mishne.probe --org org_7fa2 ast_1a2b

# One job, end to end, with progress written to job_steps as it goes.
.venv/bin/python -m mishne.orchestration.worker --org org_7fa2 job_a1b2

# The Step Functions definition, generated from the step registry. A test fails
# if the checked-in file has drifted from what this produces.
.venv/bin/python -m mishne.orchestration.statemachine > ../../infra/statemachine.json
```

### Bucket configuration

Applied per environment, idempotent, and testable against moto or MinIO without
an AWS account:

```bash
python ../../infra/s3_lifecycle.py --apply     # expiry, and the 7-day multipart abort
python ../../infra/s3_cors.py --apply --origin https://app.mishne.ai
```

### The reference run

The regression target for the whole orchestration workstream. Needs a sample,
which is not in the repository — and until C3 it was **skipping** in most runs
for exactly that reason, which is worth knowing before trusting a green suite:

```bash
MISHNE_SAMPLE_AAF=../../samples/SyncDaniel.aaf \
MISHNE_SAMPLE_REPLAY=../../samples/SyncDaniel_roughcut/work/SyncDaniel_flat_a0.asr.json \
  .venv/bin/python -m pytest tests/test_reference_run.py -q
```

`--replay` reuses a stored ASR response, so it is a ten-second test rather than
a transcription: no model is loaded and no network is touched, and every stage
after transcription runs for real.

## The pipeline, stage by stage

The registry in `pipeline/steps/__init__.py` is the list, and it is now true:
until B3 it omitted `speakers`, the AAF branch, span proposal and the transcript
page, and listed `review` — which was never built. Three things read it (the
generated state machine, the runner, the progress UI), so a test asserts it
matches what runs.

| # | Stage | File | Deterministic? |
|---|---|---|---|
| 0 | probe | `prepare.py` | yes |
| 1 | audio extract | `audio.py` | yes |
| 2 | transcribe | `transcribe.py` + `asr/` | model |
| 3 | VAD | `vad.py` | yes |
| 4 | structure into beats | `structure.py` | yes |
| — | speakers | `speakers.py` + `diarize/` | yes on multi-track |
| — | AAF ingest | `aaf_ingest.py` | yes |
| 5 | brief | `brief.py` | model, with deterministic fallback |
| 6 | propose spans | `propose.py` | gate is deterministic |
| 7 | score | `score.py` | model |
| 8 | select | `select.py` | **yes — CP-SAT solver** |
| 9 | refine cut points | `refine.py` | **yes** |
| 10 | assemble timeline | `assemble.py` | **yes** |
| 11 | emit | `emit.py` | yes |
| 12 | validate | `validate.py` | yes |

The shape that matters: **models score and propose; a solver selects; arithmetic
places the cuts.** A language model never decides a frame number.

## The load-bearing decisions

Read the ADRs before changing any of these — each records what went wrong and
why the current shape is what it is.

| ADR | Decision |
|---|---|
| [0001](adr/0001-otio-as-canonical-timeline.md) | OTIO is the record of truth; every format is a projection |
| [0002](adr/0002-workflow-engine-not-agent-framework.md) | A workflow engine, not an agent framework |
| [0003](adr/0003-managed-asr-behind-an-interface.md) | ASR behind a provider interface |
| [0004](adr/0004-constraint-solver-for-selection.md) | A solver selects, weighted by duration |
| [0005](adr/0005-audio-only-ingest-path.md) | Audio-only input must be told its rate |
| [0006](adr/0006-credit-hold-settle-ledger.md) | Credits: append-only, hold then settle |
| [0007](adr/0007-selection-as-a-swappable-stage.md) | Selection is swappable |
| [0008](adr/0008-assets-carry-their-own-coordinates.md) | No virtual timeline; beats carry their asset |
| [0009](adr/0009-diarization-per-source-region.md) | Diarize per source region; admit uncertainty |
| [0010](adr/0010-spans-not-beats.md) | Selection chooses spans; boundaries gated on silence |
| [0011](adr/0011-provider-agnostic-llm-routing.md) | Any vendor, chosen per task by policy |
| [0012](adr/0012-two-environments-and-expand-contract-migrations.md) | Two environments; every migration is backward-compatible |
| [0013](adr/0013-stage-media-to-local-disk.md) | Media is staged to a worker's disk, never mounted |
| [0014](adr/0014-linked-aaf-companions.md) | A linked AAF is accepted, and asks for the media it references |
| [0015](adr/0015-identity-behind-a-provider-interface.md) | Identity behind a provider interface; one email is one person |
| [0016](adr/0016-resume-is-re-execution.md) | Resume is idempotent re-execution, not a checkpoint restore |

## Things that will bite you

Every one of these cost real time to find.

- **`use_empty_mob_ids=True` writes an AAF that cannot be relinked.** MobIDs are
  the relink key. Attach them explicitly.
- **FCPXML is the fragile format, not AAF.** Its OTIO adapter cannot write NTSC
  rates without `interchange/fcpx_patch.py`, and patching must go through
  `otio.adapters.from_name("fcpx_xml").module()` — patching the import does
  nothing.
- **EDL carries no frame rate.** Reading one back without `rate=` silently gives
  24 fps.
- **`09:58:00;00` is not a valid drop-frame timecode.** Use the conversion pair
  in `timecode.py`; it has an exhaustive self-test.
- **Whisper word timestamps are contiguous by construction.** Word gaps are not
  a silence signal — use the VAD. This one invalidated a whole segmentation
  model.
- **ffprobe cannot read an AAF at all.** It is structured storage, not a media
  container.
- **In a real AAF the units are not uniform**: `start_time` in samples at 48 kHz
  and `duration` in frames at 25, on the same object.
- **AAF source position is timecode, not file offset.** Subtract the mob's
  `StartTime` or the flattened audio is the right length and completely silent.
- **The AAF writer rejects per-clip frame rates.** Mixed-rate projects must be
  conformed to the sequence rate at assembly.
- **Python 3.14 breaks OTIO** (`RuntimeError: bad any cast`) — wheels are
  cp39-cp313. `setup.sh` picks a supported interpreter.
- **large-v3 peaks around 4.6 GB.** It will be killed in a 4 GB container.
- **A dataclass field with a default before a required one is a TypeError at
  import.** Caught twice in this codebase.
- **The org on a request comes from the session, never from a header.** The
  `X-Org-Id` header B1 used is gone; it survives only where `use_mocks` is on,
  which `Settings` refuses outside `environment=local`.
- **An audit row written on a failing request is rolled back with it.** A failed
  login is the case that matters, and `audit.record_even_if_the_request_fails`
  is the one that opens its own transaction.
- **A ledger `delta` is the change in AVAILABLE credits, and a `settle` row is
  therefore POSITIVE.** `hold` is `-cap`, `release` is `+cap`, and `settle` is
  `+(cap - charged)` — the unused part of the hold coming back. What the
  customer paid is the hold and the settle together. Until C1 `settle` wrote
  `-charged` against a `balance_after` that had gone *up* by `cap - charged`, so
  a single row contradicted itself and summing `delta` double-counted the hold
  of every completed job. The one ledger test asserted that a hold and its
  *release* net to zero — true throughout, because release was never the broken
  case — so nothing caught it.
- **`credit_ledger` survives `purge_org`, but the `tenant` fixture resets
  `org_balances`.** The ledger is append-only and a trigger refuses the delete,
  so an org's entries are a true record of every test that has ever run while
  its balance is a true record of one. Any test asserting an absolute ledger
  total, or treating the entries as one continuous sequence, passes or fails by
  run order. Assert on the *change* across the operation under test.
- **`logging.scrub` blocks by key, and now also by suffix.** A key ending in
  `_text`, `_path`, `_filename`, `_url`, `_prompt` or `_content` is redacted
  whatever its prefix, and nested dicts and lists are walked. `_name` is
  deliberately not a blocked suffix — it would take `step_name` and
  `provider_name` with it and leave a trace of `<redacted>`, which is how a
  safeguard gets switched off by the person it inconveniences.
- **`inet` rejects a hostname.** A test client sends `testclient` as its
  address, an unparseable `X-Forwarded-For` is attacker-supplied, and writing
  either into `audit_log.ip` raises — failing the upload the customer was
  making, for the sake of a log row.
- **`projects`, `users` and `sessions` carry `org_id` but no foreign key to
  `orgs`.** Nothing cascades from an org, so deleting a tenant — and any test
  teardown — has to name each table.
- **Deleting a project used to be impossible once it had been billed for.**
  `credit_ledger.project_id` was a foreign key with `ON DELETE SET NULL`, so a
  delete made Postgres *update* the ledger — and the append-only trigger
  refused, correctly. Migration 0004 makes the ledger's ids plain columns. Two
  correct rules can forbid something between them.
- **Deleting a tenant is an ordered operation, not one statement.** Nothing
  cascades from `orgs`, and `job_assets.asset_id` is `ON DELETE RESTRICT`, so
  jobs go before assets and assets before projects. `tests/conftest.purge_org`
  is the order; `credit_ledger` and `audit_log` are append-only and are not
  deleted at all — what "delete this tenant" means for them is C4's question.
- **An AAF is never byte-identical between two runs.** Generated MobIDs and a
  modification date; same size, different bytes. Compare EDL, FCPXML, OTIO and
  the transcript byte for byte, and hold the AAF to `validate` reading it back.
- **A boto3 client built with the defaults signs with SigV2**, and every bucket
  created after 2018 rejects it — at upload time, long after the code that chose
  the signature returned. `storage.get_client` sets `s3v4` explicitly, which is
  why the storage tests exercise that client rather than one they build.
- **The part size the client slices with must be the one the server sent.** A
  client that uses its own idea of a part size uploads bytes that do not line up
  with the layout the completion is checked against, and S3 assembles them
  without complaint.
- **`projects` has no foreign key to `orgs`.** `org_id` is on every table by
  design, not by reference, so deleting an org cascades nothing. Test teardown
  and any future account deletion have to name each table.
- **An asset's rate is a placeholder until stage 0 runs.** The columns are NOT
  NULL and nothing knows the real value at upload time, so a row carries 1/1 —
  conspicuous in a UI, absurd in arithmetic — and `probed_at IS NULL` is the
  honest signal. Do not compute a duration from an unprobed asset.
- **A superuser bypasses row-level security, silently.** So does a table owner
  unless the table is `FORCE`d. Point the API at `DATABASE_URL` instead of
  `APP_DATABASE_URL` and every policy stays in the schema and stops doing
  anything. `tests/test_rls_isolation.py` asserts the connecting role can do
  neither, because otherwise the whole file passes and proves nothing.

## The three open questions, and they are one question

1. **Is the selection good enough to sell?** (Spike B, no corpus)
2. **Are the span thresholds right?** (ADR-0010, chosen by inspecting two clips)
3. **Which model is actually better per task?** (ADR-0011, compliance is
   measured, taste is not)

All three are answered by the same thing: **a corpus of raw material paired with
the editor's own finished EDL.** That is the single highest-value asset the
project does not have. See [roadmap/A1-selection-corpus.md](roadmap/A1-selection-corpus.md).
