# mishne.ai — working notes for Claude Code

## What this is

A web platform that turns raw footage into an editable rough cut. Upload video or an
AAF sequence, add director's notes, get back AAF / FCPXML / EDL plus a transcript
showing what was used and why.

**Read [docs/architecture/](docs/architecture/) before making structural changes.**
The decisions there are considered and several are load-bearing. In particular:

- [01 — Edit Engine](docs/architecture/01-edit-engine.md) is the product. Twelve
  stages, three of which call an LLM.
- [ADR-0002](docs/adr/0002-workflow-engine-not-agent-framework.md) — this is a
  workflow, not an agent. Do not introduce an agent loop into the pipeline.
- [ADR-0004](docs/adr/0004-constraint-solver-for-selection.md) — the LLM scores,
  a solver selects. Do not ask a model to hit a duration target.
- [ADR-0007](docs/adr/0007-selection-as-a-swappable-stage.md) — three job modes,
  one pipeline. Stage 7's output contract is what makes manual editing possible;
  do not widen it.

## Layout

```
apps/web        Next.js 15 · Tailwind 4 · shadcn/ui   — the customer UI (:3000)
apps/admin      Next.js 15, deliberately its own look   — the back-office (:3001)
apps/api        FastAPI · Python 3.11+ · uv           — API (:8000), admin API
                                                       (:8001, loopback), pipeline
packages/shared TypeScript types shared with the web app
docs/           Architecture and ADRs
infra/          docker-compose for local Postgres + MinIO; Terraform later
```

## Current state

*Accurate as of 31 Aug 2026. If this section and the code disagree, the code
wins — and fix this section.*

**The whole thing runs on one machine with one command: `./dev.sh`.** Postgres
and MinIO, the schema, the app login role, the three buckets with their CORS
and lifecycle rules, then the API, the web app and a local job runner. Access is
by invitation: `/signup` is closed unless `PUBLIC_SIGNUP=true`, which is how the
first owner of a deployment is made.

**The pipeline is real, end to end.** `run.py` takes a media file or an AAF to
four interchange artifacts and a transcript page. An AAF may carry its essence
or reference it: a **linked** AAF plus the folder it points at is what Media
Composer exports by default, and it is a first-class input — the media is
resolved by basename beside the AAF or one level down in `AAF Media/`, or from
`--media-dir` (ADR-0014). Every sound track is read and they are mixed for
transcription, with the per-track renders kept for speaker attribution
(ADR-0019); the cut is expressed against the first sound track. Transcription is a managed API
routed by language (ADR-0018) — xAI for what it covers, Gemini for Hebrew;
self-hosted Whisper is still one flag, for an air-gapped customer.

**The platform underneath it is built.** B1 Postgres with `org_id` everywhere and
RLS forced; B2 presigned multipart upload straight to S3/MinIO; B3 a durable
runner, generated Step Functions definition and worker image; B4 identity,
roles, sessions and an audit log; C1 Stripe behind a payment-provider interface;
C3 per-job cost as a projection of recorded model calls.

**C2 is done: the screens read the API, not fixtures.** Upload, submission,
progress, a cut edited in the browser, speaker renames and merges, artifact
download. A linked AAF's media is acquired by dropping the folder on the
requirements panel, which uploads only the files the sequence asks for; a job
may be submitted with media still missing, on an explicit acknowledgement, and
what was absent is recorded on the job. `use_mocks` still exists and is refused outside `environment=local`.

**The editor has a player, welded to the text** (ADR-0020). Every asset gets a
720p H.264 preview — AAC alone where there is no picture — in the derived
bucket, keyed on the asset so a second job finds it already made. A flat
upload's is transcoded off the pipeline entirely, queued at probe time; a
sequence's is the flattened sound mix, encoded by `stage_prepare` because that
render is the only thing there is to preview. Clicking a beat's timecode seeks
there; playing scrolls the transcript, until the reader scrolls for themselves.

**The transcode is built to run on a machine that is not the API** (ADR-0021).
`orchestration/proxyworker` builds one preview for one asset in any
environment — that is the entry point of a preview fleet.
`orchestration/proxyrunner` is the part that differs: it polls the table
locally, and long-polls SQS with `--serve`. **The asset row is the queue of
record and a message is only a wake-up**, so a lost message costs one sweep
interval rather than a preview that never arrives. A claim is a lease, and one
that expires is evidence of a dead worker.

**A platform back-office exists**, outside the tenant model: its own process on
:8001 bound to loopback, its own BYPASSRLS role, its own credential table, its
own append-only action log with a mandatory reason. It is how credits are
granted by hand until the Buy buttons are wired.

**Nothing is deployed.** No Terraform, no AWS account, no environments — but
there is CI: `.github/workflows/ci.yml` runs the suite, the typecheck, the build
and a state-machine drift check on every push. Moving to AWS + S3 is the next
infrastructure step — see
[docs/AWS-MIGRATION.md](docs/AWS-MIGRATION.md), which runs after the QA pass in
[docs/HANDOFF-CLAUDE-CODE.md](docs/HANDOFF-CLAUDE-CODE.md).

**Still open:** the browser-to-AAF path has never been clicked through end to
end against MinIO — including the folder upload, which has only been exercised
by the CLI; the player's two-way sync has been proved server-side and by
typecheck but never clicked through in a browser either (follow-scroll, the
reel switch on a mixed-rate job, and re-minting an expired URL are the parts a
test suite does not reach); the Buy buttons are inert; a multi-track cut still
emits one audio track (ADR-0019); previews are unpriced (ADR-0020); the preview fleet's queue and task
definition are Terraform that does not exist yet, so today it is still a fourth
process on one machine (ADR-0021); A1
(selection corpus) and A2 (Avid acceptance) are the two risks that can still end
the product.

## Rules that matter

**Timecode.** All internal time is rational — frames and a rate, never floats, never
`23.976`. Use `packages/shared/src/timecode.ts` on the web side and
`opentime.RationalTime` on the Python side. Drop-frame is a display convention only;
never do arithmetic on drop-frame strings. This is the single most common source of
silent bugs in this class of system.

**No customer content in logs.** Ever. No transcript text, no filenames, no brief
text. IDs, durations, counts and status only. There is a log filter; do not route
around it.

**`org_id` on every table**, including where it is derivable by join. Postgres RLS is
the backstop, not the application layer. The org comes from the request's
session and is set on the transaction with `set_config(..., is_local => true)` —
never session-level, or a pooled connection carries one tenant into the next
request with no error message anywhere.

**Media never transits the API.** Uploads go browser → S3 via presigned multipart.

**`run.py` is the specification.** The orchestrator runs the same stage
functions in the same order; where the two could drift, they share the
implementation instead (`project.stage_*`). If you change what a stage does,
change it there — not in `orchestration/graph.py`.

**A sequence's tracks are mixed for transcription, never for the cut.** All of
an AAF's sound tracks are read, asked for, and mixed into the one mono file
stage 2 transcribes. Speakers come from the per-track renders — arithmetic, not
a model. The output references `primary_clips`, the first sound track, and
widening that to N parallel tracks is a new decision, not a tidy-up. See
[ADR-0019](docs/adr/0019-mix-sound-tracks-for-transcription.md).

**ffmpeg never runs where requests are answered.** A three-hour master is about
ten minutes of every core the machine has, which is correct for a transcoder and
an outage for an API. Previews are dispatched to their own fleet and the code
does not change to put them there; if you find yourself adding CPU-bound media
work to the API or the light worker, it belongs behind the same seam. See
[ADR-0021](docs/adr/0021-previews-are-built-by-a-separate-fleet.md).

**A preview's clock is the source's clock.** The player maps `currentTime` to a
beat's timecode as `start_tc + elapsed`, with no correction term, so the
transcode never resamples the frame rate — and `proxy.verify` re-probes the
result and refuses it if the duration moved by more than a frame. A resampled
rate does not fail; it drifts, and surfaces an hour later as a player that looks
broken. `Beat.startFrames` is absolute source timecode, so the conversion is
`mediaSecondsOf` / `framesAtMediaSeconds` in `packages/shared` and nowhere else.
See [ADR-0020](docs/adr/0020-a-preview-rendition-per-asset.md).

**Stage 9 runs in every mode.** A hand-marked cut still gets silence snapping,
handles and frame quantization. The user picks *what*; stage 9 decides *where*.

**Money moves through the ledger only.** Append-only, balance is a projection,
never a mutable column. Hold at submission, settle at completion, charge
`min(actual, approved_cap)`, refund in full on failure. Never trust a
client-supplied price — recompute server-side. See
[ADR-0006](docs/adr/0006-credit-hold-settle-ledger.md).

## Design work

UI/UX and mockups are done in Claude Design. Design tokens live in
`apps/web/src/app/globals.css` under `@theme`. Change tokens there rather than
hardcoding colors in components — the whole point is that the palette can be
retuned in one place.

Component primitives in `apps/web/src/components/ui/` follow shadcn/ui conventions
(copied in, fully editable). App-specific components sit one level up in
`apps/web/src/components/`.

**The brand mark is settled** — three bars of decreasing width, three hours of
rushes down to a ten-minute cut. It lives twice: `components/logo.tsx` for the
screens, where it takes its colour from the `--primary` token, and
`app/icon.svg` for the tab, where it cannot and so carries resolved hex on a
filled ground. Changing one without the other is the bug to watch for.

## Commands

```bash
npm install            # once, from the repo root (apps/admin is a workspace)
./dev.sh               # everything: Postgres, MinIO, schema, buckets, API, web,
                       # the job runner and the preview builder
./dev.sh restart       # the same, from any state: stops whatever is already
                       # running, clears apps/web/.next, then starts
./dev.sh setup         # stop before the processes
./dev.sh api|web|worker|proxy|admin  # one of them, in its own terminal
npm run typecheck
npm run build
```

**Do not `npm run build` while `./dev.sh` or `next dev` is running.** They share
`apps/web/.next`, and the production build prunes chunks the running dev server
still has in memory — from then on every request dies with `Cannot find module
'./383.js'` and the dev server never recovers. `./dev.sh restart` is the way
back. `npm run typecheck` is safe at any time and is what you want for a quick
check anyway.

**The port goes after a `--`, or npm eats it:** `npm run dev -- --port 3000`.
Without it npm passes `3000` to `next dev` as a positional and Next reads it as
a project directory. `./dev.sh web` gets this right.

The Python side lives in `apps/api/.venv` and is not on your PATH. Prefix with
`.venv/bin/` or activate it — this is the single most common five minutes lost
by someone picking the project up.

```bash
cd apps/api && ./setup.sh
.venv/bin/alembic upgrade head
.venv/bin/python -m pytest -q
```

**`pytest -q` on a machine you have used the product with skips about fifty
tests, and they are the ones most worth running** — the whole back-office suite,
the API-parity checks, the reference run and the linked-AAF sample. Every one of
those guards is correct: `seed.reset()` is `TRUNCATE` over every table, and
clearing the platform tables would delete your back-office login. But the effect
is that "all green" can mean "the new code never ran". **Read the skip count,
not the colour.**

**`./test-all.sh` is how to actually run everything.** It creates a scratch
database, migrates it, creates the app login role, points the sample-gated tests
at `samples/`, runs the suite and drops the database afterwards. Nothing it does
can reach the database you work in, so the guards have nothing to protect and
all 681 tests execute.

```bash
cd apps/api && ./test-all.sh          # 681 tests, no skips
./test-all.sh -k billing              # arguments pass through to pytest
```

The first time those guarded tests ran they found two real defects — the seeder
not writing the proxy columns `mock.py` reports, and a test tenant whose balance
had no ledger row behind it. Tests that skip everywhere are tests that do not
exist.

**CI runs on every push** (`.github/workflows/ci.yml`): pytest against a
Postgres service container where nothing skips, `npm run typecheck` and
`npm run build`, and a check that `infra/statemachine.json` has not drifted from
the step registry.
