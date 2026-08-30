# C2 — The screens, on real data

> Thread starter. Read [../HANDOVER.md](../HANDOVER.md) first; you should not
> need any other file.

## Goal

The ten screens stop rendering fixtures and start rendering the customer's own
projects, jobs and transcripts.

## What already exists

Ten screens, complete and styled, against fixtures in `apps/web/src/lib/`:

- `app/(app)/projects/` — list, detail with assets, new job flow
- `app/(app)/jobs/[id]/` — job detail with stage progress, transcript, edit
- `app/(app)/billing/`
- `components/transcript-viewer.tsx` — beats, flags, filters, RTL-aware
- `components/cut-editor.tsx` — reorder, include/exclude, target tracking
- `components/speaker-legend.tsx` — rename speakers, merge across uploads
- `components/job-stages.tsx`, `credit-meter.tsx`, `timecode.tsx`

**Three things are already real** and are the pattern for the rest:

- **`lib/api.ts`** — one client, `credentials: "include"`, `ApiError` with a
  `retryable` flag. Everything else should go through it.
- **Auth (B4).** `login/`, `signup/` and `components/session-provider.tsx` talk
  to the real API; `(app)/layout.tsx` requires a session and `app-chrome.tsx`
  renders the real org, user and role.
- **Upload (B2).** `lib/upload.ts` and `components/asset-upload.tsx` — hashing,
  presigned multipart, per-part retry, resume across a page refresh. Wired into
  the **project page**, not into `new-job-flow.tsx`.

`packages/shared/src/types.ts` is the real contract and is current, including
multi-asset (`Job.assetIds`, `Beat.assetId`, `Transcript.assets[]`,
`Speaker.assetIds`) and the statuses B2 added (`awaiting_media`, `aaf_linked`).

## What to build

1. **Replace the fixture imports with API calls.** The types do not change. The
   read endpoints exist and `tests/test_api_parity.py` holds the fixtures and
   Postgres to identical responses, so this is a swap rather than a rewrite.
2. **Upload inside the new-job flow**, reusing `asset-upload.tsx` rather than a
   second implementation. Today the flow only picks from assets already there.
3. **Live job progress.** `job_steps` rows exist with `status`, `attempt` and a
   `detail` string the runner writes as it goes; `job-stages.tsx` renders that
   shape from fixtures. Decide how it refreshes — polling is honest and cheap
   for a job measured in minutes, and does not need the infrastructure a socket
   does.
4. **The `awaiting_media` state.** A linked AAF asks for the media it
   references, and `GET /v1/assets/{id}/requirements` returns the list, ordered
   by how many clips each file unblocks. Nothing renders it, and a sequence that
   silently waits is a customer who thinks the product is broken.
5. **Persist the cut editor.** Text-based editing is a product feature, not a
   mockup: the user marks what is in and in what order and gets an AAF back.
   `POST /v1/jobs/{id}/cut` is the endpoint and it is still a 501.
6. **Persist speaker renames and merges.** `speakers.label` and `confirmed`
   exist in the schema; nothing writes them.
7. **Artifact download.** `storage.presigned_get` sets the filename via
   Content-Disposition, and the security doc says these downloads are
   audit-logged — `audit.ARTIFACT_DOWNLOADED` is defined and unused.

## Decisions already made

- The API is the contract, and `packages/shared` is where its shape lives. A
  screen that needs a field it does not have is a change to both, not a fetch
  somewhere else.
- Fixtures stay. `use_mocks` is refused outside `environment=local`, and the
  parity test is what stops the two drifting into different products.
- **The session is client-side.** The cookie belongs to the API's origin, so a
  server component cannot read it; `session-provider` asks the browser. Do not
  reintroduce a server-side session read without solving that.

## Decisions still open

- Polling interval versus a stream for job progress.
- Whether the transcript page ships as the artifact it is today, or becomes a
  screen with the same content.
- Optimistic updates in the cut editor, which changes what a failed save means.

## Traps

- **Timecode is rational and stays rational.** `packages/shared/src/timecode.ts`
  exists because 23.976 is not 24000/1001 and a float here is a frame lost every
  42 seconds.
- **RTL is per string, not per page.** A Hebrew transcript contains Latin names
  and timecode; `dir="auto"` per string, and timecode forced LTR with
  `unicode-bidi: isolate`.
- An asset's rate is a **placeholder until it has been probed** — 1/1, with
  `probed_at` null. Do not format a timecode from an unprobed asset.
- A viewer sees the app without an upload button. That is the role model
  working, and it must look deliberate rather than broken.
- `next build` needs `node_modules` matching the machine's architecture; a
  container that borrowed a macOS install will fail on the SWC binary.

## Definition of done

- Every screen renders from the API with `USE_MOCKS=false`, and the fixtures
  still render identically with it on.
- Upload, job submission and progress work end to end from the browser.
- A cut edited in the browser produces an AAF that opens in an NLE.
- Artifact downloads are presigned and audit-logged.
- `npm run typecheck` and `npm run build` pass.
