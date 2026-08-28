# mishne.ai

AI-assisted rough-cut generation for content creators and broadcast professionals.

Upload raw footage or an AAF sequence, describe the piece you want, get back an
editable rough cut — AAF, FCPXML, EDL — plus a transcript showing exactly what was
used and why.

**mishne.ai does not produce a fine cut.** It removes the heaviest lift in post:
getting from three hours of raw material down to the ten minutes that will actually
make the cut. The editor takes it from there.

## Core thesis

The AI never touches pixels. Audio is transcribed with frame-accurate word-level
timestamps, the edit decisions are made on text, and the result is emitted as a
timeline that references the original source media by timecode. This makes the
expensive part of the problem cheap, fast, and — critically — explainable.

## Documentation

| Doc | What's in it |
|---|---|
| [00 — Overview](docs/architecture/00-overview.md) | System context, principles, component map, request flows |
| [01 — Edit Engine](docs/architecture/01-edit-engine.md) | The core IP: transcript → rough cut, stage by stage |
| [02 — Media & Interchange](docs/architecture/02-media-and-interchange.md) | Ingest, timecode, AAF/FCPXML/EDL generation, the relink problem |
| [03 — Platform & Data](docs/architecture/03-platform-and-data.md) | API, orchestration, data model, frontend, infrastructure |
| [04 — Security](docs/architecture/04-security.md) | Tenancy, encryption, retention, vendor posture, compliance path |
| [05 — Roadmap & Risks](docs/architecture/05-roadmap-and-risks.md) | Phased plan, de-risking spikes, riskiest assumptions, cost model |
| [06 — Billing & Metering](docs/architecture/06-billing-and-metering.md) | Tiers, credits, estimate/approve/hold/settle, the ledger |
| [07 — Job Modes](docs/architecture/07-job-modes.md) | AI, hybrid and manual cuts; the text-based editor |

## Decision records

| ADR | Decision |
|---|---|
| [0001](docs/adr/0001-otio-as-canonical-timeline.md) | OpenTimelineIO as the canonical internal timeline format |
| [0002](docs/adr/0002-workflow-engine-not-agent-framework.md) | Durable workflow engine, not an agent framework |
| [0003](docs/adr/0003-managed-asr-behind-an-interface.md) | Managed ASR behind a provider interface (no GPU fleet) |
| [0004](docs/adr/0004-constraint-solver-for-selection.md) | Deterministic constraint solver for segment selection |
| [0005](docs/adr/0005-audio-only-ingest-path.md) | Audio-only ingest as a first-class path |
| [0006](docs/adr/0006-credit-hold-settle-ledger.md) | Credit ledger with hold and settle |
| [0007](docs/adr/0007-selection-as-a-swappable-stage.md) | Selection is a swappable stage |

## Three ways to cut

| Mode | The user | The engine |
|---|---|---|
| **AI** | Writes director's notes | Selects, assembles, emits |
| **Hybrid** | Adjusts the proposed cut | Proposes, then assembles the user's version |
| **Manual** | Marks the cut on the transcript | Transcribes and assembles only |

Same artifacts either way. Only the selection differs — see
[07 — Job Modes](docs/architecture/07-job-modes.md).

## Repo layout

```
apps/web         Next.js 15 · Tailwind 4 · shadcn/ui
apps/api         FastAPI · Python 3.11+ · uv
packages/shared  Types, timecode and billing logic shared with the web app
docs/            Architecture and ADRs
infra/           docker-compose for local Postgres
```

## Getting started

```bash
npm install      # from the repo root
npm run dev      # web app on :3000, mock data throughout
npm run api      # FastAPI on :8000
```

## Status

Scaffolding with mock data. No pipeline yet — Spike A and Spike B come first,
see [05 — Roadmap](docs/architecture/05-roadmap-and-risks.md).
