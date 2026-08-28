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
apps/web        Next.js 15 · Tailwind 4 · shadcn/ui   — the UI
apps/api        FastAPI · Python 3.11+ · uv           — API + pipeline steps
packages/shared TypeScript types shared with the web app
docs/           Architecture and ADRs
infra/          docker-compose for local Postgres; Terraform later
```

## Current state

**Scaffolding with mock data.** Every screen renders from `apps/web/src/lib/mock-data.ts`.
The API returns the same shapes from `apps/api/src/mishne/mock.py`. No real pipeline
yet — see [05 — Roadmap](docs/architecture/05-roadmap-and-risks.md); Spike A and
Spike B come before Phase 1.

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
the backstop, not the application layer.

**Media never transits the API.** Uploads go browser → S3 via presigned multipart.

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
npm install            # once, from the repo root
npm run dev            # web app on :3000
npm run api            # FastAPI on :8000
npm run db             # local Postgres
npm run typecheck
npm run build
```
