# C2 — session prompt

Requires B1-B4. Auth and upload are already real in the web app; this is the
other eight screens.

```
We are working on mishne.ai. This session has one job: workstream C2 — the web
app on real data. Ten screens currently render fixtures; they should render the
customer's own projects, jobs and transcripts.

Read these three files first, in this order, and nothing else until you have:
  docs/HANDOVER.md                     — what exists, how to run it, the traps
  docs/roadmap/C2-web-on-real-data.md  — this workstream's brief
  apps/web/src/lib/api.ts              — the client everything goes through

Do not re-derive the architecture or read the whole codebase. The handover is
accurate and current; trust it.

## Scope

C2 only. Billing is C1 and observability is C3. If you find yourself writing a
Stripe call, stop.

Phase A (selection corpus, Avid acceptance) is deferred by decision.

## What already exists — do not rebuild it

  lib/api.ts                   one client, credentials: "include", ApiError with
                               a retryable flag
  lib/upload.ts                resumable multipart upload: hashing, per-part
  components/asset-upload.tsx  retry, resume across a refresh. Wired into the
                               PROJECT page, not into new-job-flow.tsx
  components/session-provider.tsx  the session, client-side
  components/app-chrome.tsx    real org, user and role in the header
  app/login, app/signup        real, against /v1/auth
  packages/shared/src/types.ts the contract, current, multi-asset aware
  tests/test_api_parity.py     fixtures and Postgres return identical responses

That parity test is what makes this a swap rather than a rewrite: the shapes are
already the same.

## What to build

1. Replace fixture imports with API calls, screen by screen.
2. Upload inside the new-job flow, REUSING asset-upload.tsx.
3. Live job progress from job_steps — status, attempt and a detail string the
   runner writes per stage. Decide how it refreshes and say why.
4. The awaiting_media state: a linked AAF asks for the media it references, and
   GET /v1/assets/{id}/requirements returns the list ordered by how many clips
   each file unblocks. Nothing renders it today, and a sequence that silently
   waits looks broken.
5. Persist the cut editor. POST /v1/jobs/{id}/cut is still a 501, and
   text-based editing is a product feature rather than a mockup.
6. Persist speaker renames and merges.
7. Artifact download, presigned and audit-logged.

## Decisions I have already made — do not relitigate

- The API is the contract and packages/shared is where its shape lives.
- Fixtures stay, and the parity test stays. use_mocks is refused outside
  environment=local.
- The session is client-side. The cookie belongs to the API's origin, so a
  server component cannot read it.
- One API client. Not fetch calls scattered through components.

## Decisions still open — raise them, do not quietly pick one

- Polling versus a stream for job progress. A job is minutes long; polling is
  honest and needs no infrastructure.
- Whether the transcript ships as today's standalone artifact, a screen, or both.
- Optimistic updates in the cut editor, which changes what a failed save means.

## Traps

- Timecode is rational and stays rational. 23.976 is not 24000/1001 and a float
  is a frame lost every 42 seconds. Use packages/shared/src/timecode.ts.
- RTL is per string, not per page: a Hebrew transcript contains Latin names and
  timecode. dir="auto" per string; timecode forced LTR with unicode-bidi.
- An asset's rate is a PLACEHOLDER until it has been probed — 1/1, probed_at
  null. Do not format a timecode from one.
- A viewer has no upload button. That is the role model working; make it look
  deliberate rather than broken.
- next build needs node_modules matching the machine's architecture.

## Definition of done

- Every screen renders from the API with USE_MOCKS=false, and identically from
  fixtures with it on.
- Upload, submission and progress work end to end in the browser.
- A cut edited in the browser produces an AAF that opens in an NLE.
- Artifact downloads are presigned and audit-logged.
- npm run typecheck and npm run build pass.

## Environment

  docker compose -f infra/docker-compose.yml up -d
  cd apps/api && ./setup.sh && .venv/bin/alembic upgrade head
  .venv/bin/python -m mishne.db.seed --reset
  npm run api    # USE_MOCKS=false
  npm run dev

Start by taking ONE screen — the project list — from fixtures to the API, and
show me the diff. Everything else follows that pattern, and if the pattern is
wrong I want to see it once rather than ten times.
```
