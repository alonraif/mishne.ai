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
four interchange artifacts and a transcript page. Transcription is a managed API
routed by language (ADR-0018) — xAI for what it covers, Gemini for Hebrew;
self-hosted Whisper is still one flag, for an air-gapped customer.

**The platform underneath it is built.** B1 Postgres with `org_id` everywhere and
RLS forced; B2 presigned multipart upload straight to S3/MinIO; B3 a durable
runner, generated Step Functions definition and worker image; B4 identity,
roles, sessions and an audit log; C1 Stripe behind a payment-provider interface;
C3 per-job cost as a projection of recorded model calls.

**C2 is done: the screens read the API, not fixtures.** Upload, submission,
progress, a cut edited in the browser, speaker renames and merges, artifact
download. `use_mocks` still exists and is refused outside `environment=local`.

**A platform back-office exists**, outside the tenant model: its own process on
:8001 bound to loopback, its own BYPASSRLS role, its own credential table, its
own append-only action log with a mandatory reason. It is how credits are
granted by hand until the Buy buttons are wired.

**Nothing is deployed.** No Terraform, no AWS account, no CI, no environments.
Moving to AWS + S3 is the next infrastructure step — see
[docs/AWS-MIGRATION.md](docs/AWS-MIGRATION.md), which runs after the QA pass in
[docs/HANDOFF-CLAUDE-CODE.md](docs/HANDOFF-CLAUDE-CODE.md).

**Still open:** the browser-to-AAF path has never been clicked through end to
end against MinIO; the Buy buttons are inert; A1 (selection corpus) and A2 (Avid
acceptance) are the two risks that can still end the product.

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

## Commands

```bash
npm install            # once, from the repo root (apps/admin is a workspace)
./dev.sh               # everything: Postgres, MinIO, schema, buckets, API, web, worker
./dev.sh setup         # stop before the processes
./dev.sh api|web|worker|admin      # one of them, in its own terminal
npm run typecheck
npm run build
```

The Python side lives in `apps/api/.venv` and is not on your PATH. Prefix with
`.venv/bin/` or activate it — this is the single most common five minutes lost
by someone picking the project up.

```bash
cd apps/api && ./setup.sh
.venv/bin/alembic upgrade head
.venv/bin/python -m pytest -q
```

The suite runs against the local Postgres in `.env`, and the API-parity tests
re-seed it — `seed.reset()` is `TRUNCATE` over every table. They now skip when
the database holds an organisation the suite did not create, so real work is
safe, but that also means those tests are skipped rather than run on a machine
you have been using the product with. A scratch `DATABASE_URL` gets them back.
